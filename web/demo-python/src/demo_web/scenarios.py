"""Pre-defined load scenarios run from the terminal.

Usage (inside the demo-web container): ``python -m demo_web.scenarios [scenario]``.
Each run creates and publishes a GESAD/UECE event with one free activity, sends
registrations with the synthetic scenario participants and prints the outcome.
Without a scenario, all of them run in parallel together with a random mixed
load (3 events x 3 activities, paid seats, cancellations and waiting-list offers).
The organizer can follow the run live on the Reports page.
"""

import argparse
import asyncio
import json
import os
import random
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from time import monotonic
from typing import Awaitable, Callable, Mapping, Sequence
from uuid import UUID, uuid4

from demo_web.clients import DemoClients
from demo_web.errors import InvalidResponseError, ServiceError, TransportError
from demo_web.models import ActivityInput, Availability, EventInput, Money
from demo_web.settings import Settings, SettingsError


MIXED_LOAD_PARTICIPANTS = 20
MAX_PEAK_DURATION_SECONDS = 300
DEFAULT_PEAK_PARTICIPANTS = 20
DEFAULT_PEAK_DURATION_SECONDS = 30
SERVICE_ERRORS = (ServiceError, TransportError, InvalidResponseError)


@dataclass(frozen=True)
class Scenario:
    key: str
    title: str
    description: str
    capacity: int


SCENARIOS = {
    "pico-inscricoes": Scenario(
        "pico-inscricoes",
        "Pico de inscrições",
        "Muitos participantes se inscrevem em poucos segundos numa atividade de 5 vagas.",
        capacity=5,
    ),
    "ultima-vaga": Scenario(
        "ultima-vaga",
        "Última vaga",
        "Dois participantes disputam, ao mesmo tempo, a única vaga de uma atividade.",
        capacity=1,
    ),
    "envios-repetidos": Scenario(
        "envios-repetidos",
        "Envios repetidos",
        "Um participante envia a mesma inscrição 10 vezes ao mesmo tempo (duplo clique).",
        capacity=1,
    ),
}
DUPLICATE_SUBMISSIONS = 10


class ScenarioConfigError(ValueError):
    """Raised when the scenario configuration is invalid."""


@dataclass(frozen=True)
class Submission:
    participant: str
    token: str
    idempotency_key: str
    delay_seconds: float


@dataclass(frozen=True)
class Outcome:
    participant: str
    elapsed_seconds: float
    status: str | None = None
    registration_id: UUID | None = None
    error_code: str | None = None


@dataclass(frozen=True)
class Verdict:
    passed: bool
    lines: tuple[str, ...]


def participant_label(index: int) -> str:
    return f"Participante de cenário {index:02}"


def _bounded_integer(environ: Mapping[str, str], key: str, default: int, maximum: int) -> int:
    raw = environ.get(key, "").strip()
    if not raw:
        return default
    if not raw.isdigit() or not 1 <= int(raw) <= maximum:
        raise ScenarioConfigError(f"{key} must be an integer between 1 and {maximum}.")
    return int(raw)


def peak_settings(environ: Mapping[str, str], pool_size: int) -> tuple[int, int]:
    """Return (participants, duration_seconds) for the registration peak."""
    participants = _bounded_integer(
        environ,
        "SCENARIO_PEAK_PARTICIPANTS",
        min(DEFAULT_PEAK_PARTICIPANTS, pool_size),
        pool_size,
    )
    duration = _bounded_integer(
        environ,
        "SCENARIO_PEAK_DURATION_SECONDS",
        DEFAULT_PEAK_DURATION_SECONDS,
        MAX_PEAK_DURATION_SECONDS,
    )
    return participants, duration


def plan_submissions(
    scenario: Scenario,
    identities: Sequence[Mapping[str, str]],
    run_id: UUID,
    *,
    participants: int = DEFAULT_PEAK_PARTICIPANTS,
    duration_seconds: int = DEFAULT_PEAK_DURATION_SECONDS,
) -> list[Submission]:
    if scenario.key == "pico-inscricoes":
        count = min(participants, len(identities))
        spacing = duration_seconds / count
        return [
            Submission(
                participant_label(index + 1),
                identities[index]["token"],
                f"scenario-{run_id}-{index + 1:02}",
                round(index * spacing, 3),
            )
            for index in range(count)
        ]
    if scenario.key == "ultima-vaga":
        return [
            Submission(
                participant_label(index + 1),
                identities[index]["token"],
                f"scenario-{run_id}-{index + 1:02}",
                0.0,
            )
            for index in range(2)
        ]
    if scenario.key == "envios-repetidos":
        key = f"scenario-{run_id}-duplicate"
        return [
            Submission(participant_label(1), identities[0]["token"], key, 0.0)
            for _ in range(DUPLICATE_SUBMISSIONS)
        ]
    raise ScenarioConfigError(f"Unknown scenario {scenario.key!r}.")


def _error_code(error: Exception) -> str:
    if getattr(error, "outcome_unknown", False):
        return "OUTCOME_UNKNOWN"
    code = getattr(error, "code", "REQUEST_FAILED")
    return code if isinstance(code, str) and code.isascii() else "REQUEST_FAILED"


async def prepare_activity(
    clients,
    *,
    scenario: Scenario,
    organizer_token: str,
    organization_id: str,
    run_id: UUID,
    now: datetime,
):
    """Create and publish the scenario event with a single free activity."""
    starts_at = now + timedelta(minutes=15)
    ends_at = starts_at + timedelta(hours=1)
    stamp = now.strftime("%H:%M:%S")
    title = f"[CENÁRIO] {scenario.title} · {stamp}"
    event = await clients.create_event(
        EventInput(
            organizationId=UUID(organization_id),
            title=title,
            description=f"Evento fictício do GESAD/UECE gerado pelo script de cenário. "
            f"{scenario.description}",
            startsAt=starts_at,
            endsAt=ends_at + timedelta(minutes=5),
        ),
        token=organizer_token,
        idempotency_key=f"scenario-{run_id}-event",
    )
    activity = await clients.create_activity(
        event.id,
        ActivityInput(
            title=f"[CENÁRIO] {scenario.title}",
            startsAt=starts_at,
            endsAt=ends_at,
            location="Laboratório GESAD/UECE",
            responsible="Equipe GESAD/UECE",
            capacity=scenario.capacity,
            price=Money(amountMinor=0, currency="BRL"),
        ),
        token=organizer_token,
        idempotency_key=f"scenario-{run_id}-activity",
    )
    await clients.publish_event(
        event.id,
        token=organizer_token,
        idempotency_key=f"scenario-{run_id}-publish",
    )
    return event, activity


async def run_submissions(
    clients,
    activity_id: UUID,
    submissions: Sequence[Submission],
    *,
    on_outcome: Callable[[Outcome], None] = lambda outcome: None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    clock: Callable[[], float] = monotonic,
) -> list[Outcome]:
    started = clock()

    async def send(submission: Submission) -> Outcome:
        await sleep(submission.delay_seconds)
        try:
            registration = await clients.request_registration(
                activity_id,
                token=submission.token,
                idempotency_key=submission.idempotency_key,
                correlation_id=uuid4(),
            )
        except SERVICE_ERRORS as error:
            outcome = Outcome(
                submission.participant,
                round(clock() - started, 1),
                error_code=_error_code(error),
            )
        else:
            outcome = Outcome(
                submission.participant,
                round(clock() - started, 1),
                status=registration.status,
                registration_id=registration.id,
            )
        on_outcome(outcome)
        return outcome

    return list(await asyncio.gather(*(send(item) for item in submissions)))


def evaluate(
    scenario: Scenario,
    outcomes: Sequence[Outcome],
    availability: Availability | None,
) -> Verdict:
    statuses = Counter(item.status for item in outcomes if item.status)
    errors = Counter(item.error_code for item in outcomes if item.error_code)
    lines = [
        "Resultado: "
        + (", ".join(f"{count} {status}" for status, count in sorted(statuses.items())) or "nenhum")
    ]
    if errors:
        lines.append("Erros: " + ", ".join(f"{count} {code}" for code, count in errors.items()))
    passed = not errors
    if availability is not None:
        within = availability.occupied <= availability.capacity
        passed = passed and within
        lines.append(
            f"Vagas ocupadas: {availability.occupied} de {availability.capacity} — "
            + ("sem overbooking." if within else "OVERBOOKING!")
        )
    else:
        passed = False
        lines.append("Não foi possível ler a disponibilidade final.")

    if scenario.key == "pico-inscricoes":
        expected_confirmed = min(scenario.capacity, len(outcomes))
        ok = statuses.get("CONFIRMED", 0) == expected_confirmed
        lines.append(
            f"Esperado: {expected_confirmed} CONFIRMED e o restante na lista de espera."
        )
    elif scenario.key == "ultima-vaga":
        ok = statuses.get("CONFIRMED", 0) == 1 and statuses.get("WAITLISTED", 0) == 1
        lines.append("Esperado: exatamente 1 CONFIRMED e 1 WAITLISTED.")
    else:
        ids = {item.registration_id for item in outcomes if item.registration_id}
        ok = len(ids) == 1
        lines.append(
            f"Inscrições distintas criadas: {len(ids)} (esperado: 1, mesma chave de idempotência)."
        )
    passed = passed and ok
    lines.append("Veredito: OK" if passed else "Veredito: VERIFICAR")
    return Verdict(passed, tuple(lines))


def _load_identities(settings: Settings) -> tuple[dict[str, str], list[dict[str, str]]]:
    fixtures = json.loads(settings.identity_fixtures_path.read_text(encoding="utf-8"))
    if not settings.lab_enabled or settings.lab_fixtures_path is None:
        raise ScenarioConfigError(
            "Scenario scripts need DEMO_LAB_ENABLED=true and IDENTITY_LAB_ENABLED=true."
        )
    pool = json.loads(settings.lab_fixtures_path.read_text(encoding="utf-8"))
    names = sorted(pool, key=lambda name: int(name.removeprefix("scenarioParticipant")))
    identities = [dict(pool[name]) for name in names]
    return dict(fixtures["organizer"]), identities


def _load_configuration(environ: Mapping[str, str]):
    settings = Settings.from_environment(environ)
    organizer, identities = _load_identities(settings)
    participants, duration = peak_settings(environ, len(identities))
    return settings, organizer, identities, participants, duration


async def _run_single(
    scenario: Scenario,
    clients,
    organizer: Mapping[str, str],
    identities: Sequence[Mapping[str, str]],
    *,
    participants: int,
    duration: int,
    out: Callable[[str], None],
    sleep: Callable[[float], Awaitable[None]],
    now: Callable[[], datetime],
) -> bool:
    run_id = uuid4()
    submissions = plan_submissions(
        scenario,
        identities,
        run_id,
        participants=participants,
        duration_seconds=duration,
    )
    out(f"Cenário: {scenario.title}")
    out(scenario.description)
    if scenario.key == "pico-inscricoes":
        out(f"{len(submissions)} participantes em {duration} s · {scenario.capacity} vagas")

    try:
        event, activity = await prepare_activity(
            clients,
            scenario=scenario,
            organizer_token=organizer["token"],
            organization_id=organizer["organizationId"],
            run_id=run_id,
            now=now(),
        )
    except SERVICE_ERRORS as error:
        out(f"Não foi possível preparar o evento: {_error_code(error)}")
        return False
    out(f"Evento publicado: {event.title}")
    out("Acompanhe em Relatórios, entrando como Organizador — GESAD/UECE.")
    out("")

    def show(outcome: Outcome) -> None:
        result = outcome.status or f"erro {outcome.error_code}"
        out(f"[{outcome.elapsed_seconds:5.1f}s] {outcome.participant} → {result}")

    outcomes = await run_submissions(
        clients, activity.id, submissions, on_outcome=show, sleep=sleep
    )
    try:
        availability = await clients.get_availability(activity.id)
    except SERVICE_ERRORS:
        availability = None

    verdict = evaluate(scenario, outcomes, availability)
    out("")
    for line in verdict.lines:
        out(line)
    return verdict.passed


async def run_scenario(
    scenario_key: str,
    *,
    environ: Mapping[str, str],
    out: Callable[[str], None] = print,
    clients_factory: Callable[[Settings], DemoClients] = DemoClients,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> int:
    scenario = SCENARIOS[scenario_key]
    try:
        settings, organizer, identities, participants, duration = _load_configuration(environ)
    except (SettingsError, ScenarioConfigError, OSError, KeyError, ValueError) as error:
        out(f"Erro de configuração: {error}")
        return 2

    clients = clients_factory(settings)
    try:
        passed = await _run_single(
            scenario,
            clients,
            organizer,
            identities,
            participants=participants,
            duration=duration,
            out=out,
            sleep=sleep,
            now=now,
        )
    finally:
        await clients.aclose()
    return 0 if passed else 1


MIXED_KEY = "carga-mista"
DEFAULT_MIXED_DURATION_SECONDS = 60
MIN_MIXED_DURATION_SECONDS = 10
MIXED_CANCEL_RATE = 0.2
MIXED_EVENTS = (
    (
        "Semana de Engenharia de Software",
        ("Palestra: Microsserviços", "Oficina: Testes automatizados", "Mesa-redonda: Carreira em TI"),
    ),
    (
        "Workshop de Arquitetura em Nuvem",
        ("Minicurso: Docker e Compose", "Oficina: Mensageria com RabbitMQ", "Palestra: Observabilidade"),
    ),
    (
        "Encontro de Pesquisa GESAD",
        ("Painel: Pesquisa em ES", "Oficina: Escrita científica", "Sessão de pôsteres"),
    ),
)
MIXED_PRICES_MINOR = (2000, 3500, 5000)
PARALLEL_KEYS = ("pico-inscricoes", "ultima-vaga", "envios-repetidos", MIXED_KEY)


@dataclass(frozen=True)
class MixedActivity:
    event_index: int
    title: str
    capacity: int
    price_minor: int


@dataclass(frozen=True)
class MixedAction:
    participant_index: int
    activity_index: int
    delay_seconds: float
    cancel_after_seconds: float | None


@dataclass(frozen=True)
class MixedPlan:
    activities: tuple[MixedActivity, ...]
    actions: tuple[MixedAction, ...]


def mixed_duration(environ: Mapping[str, str]) -> int:
    duration = _bounded_integer(
        environ,
        "SCENARIO_MIXED_DURATION_SECONDS",
        DEFAULT_MIXED_DURATION_SECONDS,
        MAX_PEAK_DURATION_SECONDS,
    )
    if duration < MIN_MIXED_DURATION_SECONDS:
        raise ScenarioConfigError(
            "SCENARIO_MIXED_DURATION_SECONDS must be an integer between "
            f"{MIN_MIXED_DURATION_SECONDS} and {MAX_PEAK_DURATION_SECONDS}."
        )
    return duration


def plan_mixed_load(participants: int, duration_seconds: int, rng: random.Random) -> MixedPlan:
    """Random registrations over 3 events x 3 activities, with some cancellations."""
    activities = []
    for event_index, (_, titles) in enumerate(MIXED_EVENTS):
        paid_index = rng.randrange(len(titles))
        for index, title in enumerate(titles):
            activities.append(
                MixedActivity(
                    event_index,
                    title,
                    capacity=rng.randint(2, 5),
                    price_minor=rng.choice(MIXED_PRICES_MINOR) if index == paid_index else 0,
                )
            )
    popularity = [rng.randint(1, 5) for _ in activities]
    register_window = duration_seconds * 0.7
    actions = []
    for participant_index in range(participants):
        target = rng.randint(2, 4)
        chosen: list[int] = []
        while len(chosen) < target:
            pick = rng.choices(range(len(activities)), weights=popularity)[0]
            if pick not in chosen:
                chosen.append(pick)
        for activity_index in chosen:
            delay = round(rng.uniform(0, register_window), 1)
            cancel_after = None
            if activities[activity_index].price_minor == 0 and rng.random() < MIXED_CANCEL_RATE:
                cancel_after = round(rng.uniform(2, max(2, duration_seconds - delay - 3)), 1)
            actions.append(MixedAction(participant_index, activity_index, delay, cancel_after))
    actions.sort(key=lambda item: item.delay_seconds)
    return MixedPlan(tuple(activities), tuple(actions))


async def _prepare_mixed_events(clients, plan: MixedPlan, organizer, run_id: UUID, now: datetime):
    stamp = now.strftime("%H:%M:%S")
    created = []
    for event_index, (event_title, _) in enumerate(MIXED_EVENTS):
        starts_at = now + timedelta(minutes=15, hours=4 * event_index)
        event = await clients.create_event(
            EventInput(
                organizationId=UUID(organizer["organizationId"]),
                title=f"[CENÁRIO] Carga mista · {event_title} · {stamp}",
                description="Evento fictício do GESAD/UECE gerado pelo script de cenário "
                "(carga mista: inscrições, desistências e lista de espera).",
                startsAt=starts_at,
                endsAt=starts_at + timedelta(hours=3, minutes=5),
            ),
            token=organizer["token"],
            idempotency_key=f"scenario-{run_id}-event-{event_index}",
        )
        slot = 0
        for activity_index, spec in enumerate(plan.activities):
            if spec.event_index != event_index:
                continue
            activity_start = starts_at + timedelta(hours=slot)
            slot += 1
            activity = await clients.create_activity(
                event.id,
                ActivityInput(
                    title=f"[CENÁRIO] {spec.title}",
                    startsAt=activity_start,
                    endsAt=activity_start + timedelta(minutes=55),
                    location="Laboratório GESAD/UECE",
                    responsible="Equipe GESAD/UECE",
                    capacity=spec.capacity,
                    price=Money(amountMinor=spec.price_minor, currency="BRL"),
                ),
                token=organizer["token"],
                idempotency_key=f"scenario-{run_id}-activity-{activity_index}",
            )
            created.append(activity)
        await clients.publish_event(
            event.id,
            token=organizer["token"],
            idempotency_key=f"scenario-{run_id}-publish-{event_index}",
        )
    return created


async def _run_mixed_load(
    clients,
    organizer: Mapping[str, str],
    identities: Sequence[Mapping[str, str]],
    *,
    duration: int,
    out: Callable[[str], None],
    sleep: Callable[[float], Awaitable[None]],
    now: Callable[[], datetime],
    rng: random.Random,
    clock: Callable[[], float] = monotonic,
) -> bool:
    identities = identities[:MIXED_LOAD_PARTICIPANTS]
    plan = plan_mixed_load(len(identities), duration, rng)
    run_id = uuid4()
    paid = sum(spec.price_minor > 0 for spec in plan.activities)
    out("Cenário: Carga mista")
    out(
        f"{len(MIXED_EVENTS)} eventos · {len(plan.activities)} atividades ({paid} pagas) · "
        f"{len(plan.actions)} inscrições e desistências aleatórias em {duration} s"
    )
    try:
        activities = await _prepare_mixed_events(clients, plan, organizer, run_id, now())
    except SERVICE_ERRORS as error:
        out(f"Não foi possível preparar os eventos: {_error_code(error)}")
        return False
    out("Eventos publicados: [CENÁRIO] Carga mista · …")
    out("")

    started = clock()
    statuses: Counter[str] = Counter()
    errors: Counter[str] = Counter()
    cancelled = 0
    promoted = 0
    waiting: dict[int, list[tuple[UUID, Mapping[str, str], str]]] = {}

    def log(participant: str, activity_index: int, text: str) -> None:
        title = plan.activities[activity_index].title
        out(f"[{clock() - started:5.1f}s] {participant} · {title} → {text}")

    async def promote(activity_index: int) -> None:
        nonlocal promoted
        for _ in range(3):
            await sleep(1)
            for entry in list(waiting.get(activity_index, ())):
                registration_id, identity, participant = entry
                try:
                    current = await clients.get_registration(
                        registration_id, token=identity["token"]
                    )
                    if current.status != "OFFERED":
                        continue
                    accepted = await clients.accept_offer(
                        registration_id,
                        token=identity["token"],
                        idempotency_key=f"scenario-{run_id}-accept-{registration_id}",
                    )
                except SERVICE_ERRORS as error:
                    errors[_error_code(error)] += 1
                    log(participant, activity_index, f"erro {_error_code(error)}")
                    return
                waiting[activity_index].remove(entry)
                promoted += 1
                log(participant, activity_index, f"saiu da lista de espera → {accepted.status}")
                return

    async def act(index: int, action: MixedAction) -> None:
        nonlocal cancelled
        await sleep(action.delay_seconds)
        identity = identities[action.participant_index]
        participant = participant_label(action.participant_index + 1)
        activity_id = activities[action.activity_index].id
        try:
            registration = await clients.request_registration(
                activity_id,
                token=identity["token"],
                idempotency_key=f"scenario-{run_id}-{index:03}",
                correlation_id=uuid4(),
            )
        except SERVICE_ERRORS as error:
            errors[_error_code(error)] += 1
            log(participant, action.activity_index, f"erro {_error_code(error)}")
            return
        statuses[registration.status] += 1
        log(participant, action.activity_index, registration.status)
        if registration.status == "WAITLISTED":
            waiting.setdefault(action.activity_index, []).append(
                (registration.id, identity, participant)
            )
        if registration.status != "CONFIRMED" or action.cancel_after_seconds is None:
            return
        await sleep(action.cancel_after_seconds)
        try:
            await clients.cancel_registration(
                registration.id,
                token=identity["token"],
                idempotency_key=f"scenario-{run_id}-cancel-{index:03}",
            )
        except SERVICE_ERRORS as error:
            errors[_error_code(error)] += 1
            log(participant, action.activity_index, f"erro {_error_code(error)}")
            return
        cancelled += 1
        log(participant, action.activity_index, "desistiu (CANCELLED) · vaga liberada")
        await promote(action.activity_index)

    await asyncio.gather(*(act(index, action) for index, action in enumerate(plan.actions)))

    overbooked = 0
    unreadable = 0
    for activity in activities:
        try:
            availability = await clients.get_availability(activity.id)
        except SERVICE_ERRORS:
            unreadable += 1
            continue
        overbooked += availability.occupied > availability.capacity
    out("")
    out(
        "Resultado: "
        + (", ".join(f"{count} {status}" for status, count in sorted(statuses.items())) or "nenhum")
    )
    out(f"Desistências: {cancelled} · vagas repassadas da lista de espera: {promoted}")
    if errors:
        out("Erros: " + ", ".join(f"{count} {code}" for code, count in errors.items()))
    if unreadable:
        out(f"Não foi possível ler a disponibilidade de {unreadable} atividade(s).")
    out(
        f"Atividades sem overbooking: {len(activities) - overbooked - unreadable} "
        f"de {len(activities)}."
    )
    passed = not errors and not overbooked and not unreadable
    out("Veredito: OK" if passed else "Veredito: VERIFICAR")
    return passed


async def run_all(
    *,
    environ: Mapping[str, str],
    out: Callable[[str], None] = print,
    clients_factory: Callable[[Settings], DemoClients] = DemoClients,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    rng: random.Random | None = None,
) -> int:
    """Run every scenario in parallel, plus a random mixed load."""
    try:
        settings, organizer, identities, participants, _ = _load_configuration(environ)
        duration = mixed_duration(environ)
    except (SettingsError, ScenarioConfigError, OSError, KeyError, ValueError) as error:
        out(f"Erro de configuração: {error}")
        return 2

    width = max(len(key) for key in PARALLEL_KEYS)

    def prefixed(key: str) -> Callable[[str], None]:
        return lambda line: line and out(f"[{key:<{width}}] {line}")

    out(f"Todos os cenários em paralelo, durante cerca de {duration} s")
    out("Acompanhe em Relatórios, entrando como Organizador — GESAD/UECE.")
    out("")
    clients = clients_factory(settings)
    try:
        single = [
            _run_single(
                SCENARIOS[key],
                clients,
                organizer,
                identities,
                participants=participants,
                duration=duration,
                out=prefixed(key),
                sleep=sleep,
                now=now,
            )
            for key in PARALLEL_KEYS[:-1]
        ]
        mixed = _run_mixed_load(
            clients,
            organizer,
            identities,
            duration=duration,
            out=prefixed(MIXED_KEY),
            sleep=sleep,
            now=now,
            rng=rng or random.Random(),
        )
        results = await asyncio.gather(*single, mixed)
    finally:
        await clients.aclose()

    out("")
    out("Resumo")
    for key, passed in zip(PARALLEL_KEYS, results):
        out(f"  {key:<{width}} {'OK' if passed else 'VERIFICAR'}")
    out("Veredito geral: OK" if all(results) else "Veredito geral: VERIFICAR")
    return 0 if all(results) else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m demo_web.scenarios",
        description="Run a pre-defined EventHub load scenario.",
    )
    parser.add_argument(
        "scenario",
        nargs="?",
        choices=sorted(SCENARIOS),
        help="omit to run every scenario in parallel with a random mixed load",
    )
    args = parser.parse_args(argv)
    if args.scenario is None:
        return asyncio.run(run_all(environ=os.environ))
    return asyncio.run(run_scenario(args.scenario, environ=os.environ))


if __name__ == "__main__":
    sys.exit(main())
