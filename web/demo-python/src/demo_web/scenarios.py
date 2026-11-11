"""Pre-defined load scenarios run from the terminal.

Usage (inside the demo-web container): ``python -m demo_web.scenarios <scenario>``.
Each run creates and publishes a GESAD/UECE event with one free activity, sends
registrations with the synthetic scenario participants and prints the outcome.
The organizer can follow the run live on the Reports page.
"""

import argparse
import asyncio
import json
import os
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


MAX_SCENARIO_PARTICIPANTS = 20
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
        min(MAX_SCENARIO_PARTICIPANTS, pool_size),
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
    identities = [dict(pool[name]) for name in sorted(pool)][:MAX_SCENARIO_PARTICIPANTS]
    return dict(fixtures["organizer"]), identities


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
        settings = Settings.from_environment(environ)
        organizer, identities = _load_identities(settings)
        participants, duration = peak_settings(environ, len(identities))
    except (SettingsError, ScenarioConfigError, OSError, KeyError, ValueError) as error:
        out(f"Erro de configuração: {error}")
        return 2

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

    clients = clients_factory(settings)
    try:
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
            return 1
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
    finally:
        await clients.aclose()

    verdict = evaluate(scenario, outcomes, availability)
    out("")
    for line in verdict.lines:
        out(line)
    return 0 if verdict.passed else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m demo_web.scenarios",
        description="Run a pre-defined EventHub load scenario.",
    )
    parser.add_argument("scenario", choices=sorted(SCENARIOS))
    args = parser.parse_args(argv)
    return asyncio.run(run_scenario(args.scenario, environ=os.environ))


if __name__ == "__main__":
    sys.exit(main())
