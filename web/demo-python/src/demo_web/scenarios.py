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
import math
import os
import random
import sys
from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from time import monotonic
from typing import Awaitable, Callable, Mapping, Sequence
from uuid import UUID, uuid4

from demo_web.clients import DemoClients
from demo_web.errors import InvalidResponseError, ServiceError, TransportError
from demo_web.models import ActivityInput, Availability, EventInput, Money
from demo_web.settings import Settings, SettingsError


PEAK_SEAT_RATIO = 0.25
MIXED_BASE_PARTICIPANTS = 20
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
        "Muitos participantes se inscrevem em poucos segundos numa atividade com vagas "
        "para 25% deles.",
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


def scenario_clients(settings: Settings) -> DemoClients:
    return DemoClients(settings, wait_for_connection=True)


TRANSPORT_RESENDS = 3


class ResendingClients:
    """Resend a call after a timeout or lost connection, keeping its Idempotency-Key.

    Every scenario write carries an Idempotency-Key, so under saturation a resend
    either replays the stored result or performs the write exactly once.
    """

    def __init__(self, clients, sleep: Callable[[float], Awaitable[None]]):
        self._clients = clients
        self._sleep = sleep
        self.resent = 0

    async def aclose(self) -> None:
        await self._clients.aclose()

    def __getattr__(self, name: str):
        method = getattr(self._clients, name)
        if not asyncio.iscoroutinefunction(method):
            return method

        async def call(*args, **kwargs):
            for attempt in range(TRANSPORT_RESENDS + 1):
                try:
                    return await method(*args, **kwargs)
                except TransportError:
                    if attempt == TRANSPORT_RESENDS:
                        raise
                    self.resent += 1
                    await self._sleep(1)

        return call


METRICS_ENV = "SCENARIO_METRICS"


class RequestMetrics:
    """Collects the service time of every scenario request for scripts/benchmark.sh.

    Times start when the request leaves the local connection queue, so they measure
    the services, not the scenario's own backlog. Timeouts, lost connections and
    5xx answers count as failures; 4xx answers are business outcomes.
    """

    def __init__(self, clock: Callable[[], float] = monotonic):
        self._clock = clock
        self._started = clock()
        self._latencies: dict[str, list[int]] = {}
        self._failures: Counter[str] = Counter()

    def record(self, entry) -> None:
        self._latencies.setdefault(entry.service, []).append(entry.elapsed_ms)
        if entry.status is None or entry.status >= 500:
            self._failures[entry.service] += 1

    def summary(self, resent: int) -> dict[str, object]:
        elapsed = max(self._clock() - self._started, 1e-6)
        every = [value for values in self._latencies.values() for value in values]
        return {
            "durationSeconds": round(elapsed, 1),
            "requests": len(every),
            "requestsPerSecond": round(len(every) / elapsed, 1),
            "failures": sum(self._failures.values()),
            "resent": resent,
            "latencyMs": _latency_percentiles(every),
            "services": {
                service: {
                    "requests": len(values),
                    "failures": self._failures[service],
                    "latencyMs": _latency_percentiles(values),
                }
                for service, values in sorted(self._latencies.items())
            },
        }


def _latency_percentiles(values: Sequence[int]) -> dict[str, int | None]:
    if not values:
        return {"p50": None, "p95": None, "p99": None, "max": None}
    ordered = sorted(values)

    def nearest_rank(share: float) -> int:
        return ordered[max(0, math.ceil(share * len(ordered)) - 1)]

    return {
        "p50": nearest_rank(0.5),
        "p95": nearest_rank(0.95),
        "p99": nearest_rank(0.99),
        "max": ordered[-1],
    }


def _report_resends(clients: ResendingClients, out: Callable[[str], None]) -> None:
    if clients.resent:
        out(f"Reenvios após timeout (mesma Idempotency-Key): {clients.resent}")


def participant_label(index: int) -> str:
    return f"Participante de cenário {index:02}"


def _bounded_integer(environ: Mapping[str, str], key: str, default: int, maximum: int) -> int:
    raw = environ.get(key, "").strip()
    if not raw:
        return default
    if not raw.isdigit() or not 1 <= int(raw) <= maximum:
        raise ScenarioConfigError(f"{key} must be an integer between 1 and {maximum}.")
    return int(raw)


def peak_capacity(participants: int) -> int:
    """Seats of the peak activity: 25% of the participants, at least one."""
    return max(1, math.ceil(participants * PEAK_SEAT_RATIO))


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
    if scenario.key == "pico-inscricoes":
        scenario = replace(scenario, capacity=peak_capacity(len(submissions)))
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
    clients_factory: Callable[[Settings], DemoClients] = scenario_clients,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> int:
    scenario = SCENARIOS[scenario_key]
    try:
        settings, organizer, identities, participants, duration = _load_configuration(environ)
    except (SettingsError, ScenarioConfigError, OSError, KeyError, ValueError) as error:
        out(f"Erro de configuração: {error}")
        return 2

    clients = ResendingClients(clients_factory(settings), sleep)
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
    _report_resends(clients, out)
    return 0 if passed else 1


MIXED_KEY = "carga-mista"
DEFAULT_MIXED_DURATION_SECONDS = 60
MIN_MIXED_DURATION_SECONDS = 10
MIXED_CANCEL_RATE = 0.2
# Share of waiting-list offers the participant ignores; Registration expires them
# after WAITLIST_OFFER_EXPIRY_MINUTES.
MIXED_OFFER_LAPSE_RATE = 0.2
# Errors that mean an accepted offer had already expired (see promote()).
LATE_OFFER_CODES = frozenset({"OFFER_EXPIRED", "INVALID_STATE"})
MIXED_CHECK_IN_RATES = (0.4, 0.9)
MIXED_CERTIFICATE_RATES = (0.5, 1.0)
# Staggered timeline, as fractions of the run: activity k of event e starts at
# FIRST_START + e * EVENT_STEP + k * ACTIVITY_STEP and lasts ACTIVITY_LENGTH.
# Under heavy load an activity starts later: the organizer only opens it after
# its registrations, cancellations and promotions finish, and its check-ins and
# certificates shift by the same delay.
MIXED_FIRST_START = 0.40
MIXED_EVENT_STEP = 0.09
MIXED_ACTIVITY_STEP = 0.03
MIXED_ACTIVITY_LENGTH = 0.18
MIXED_CERTIFICATE_DEADLINE = 0.97
# Registration rejects admissions, cancellations and offers once an activity starts.
MIXED_ADMISSION_MARGIN_SECONDS = 3.0
MIXED_PROMOTION_SECONDS = 4.0
MIXED_PREPARATION_SECONDS = 5.0
# Until opened, activities sit this far in the future so registrations are never late.
MIXED_PARKED_SECONDS = 3600
# One attempt per second; under peak load Registration events can reach
# Attendance tens of seconds late, so give propagation about a minute and a half.
RETRY_ATTEMPTS = 90
PAYMENT_CONFIRMATION_ATTEMPTS = 20
# Attendance learns about registrations and activities asynchronously, and the
# service clocks can differ slightly from the script; these clear on retry.
CHECK_IN_RETRYABLE = frozenset({"ELIGIBILITY_NOT_READY", "REGISTRATION_NOT_CONFIRMED"})
CERTIFICATE_RETRYABLE = CHECK_IN_RETRYABLE | {"ACTIVITY_NOT_ENDED"}
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
    starts_at_seconds: float = 0.0
    ends_at_seconds: float = 0.0
    check_in_rate: float = 0.0
    certificate_rate: float = 0.0


@dataclass(frozen=True)
class MixedAction:
    """Times are seconds from the start of the run, except cancel_after_seconds."""

    participant_index: int
    activity_index: int
    delay_seconds: float
    cancel_after_seconds: float | None
    check_in_at_seconds: float | None = None
    certificate_at_seconds: float | None = None
    ignores_offer: bool = False


@dataclass(frozen=True)
class MixedPlan:
    activities: tuple[MixedActivity, ...]
    actions: tuple[MixedAction, ...]


@dataclass(frozen=True)
class MixedRates:
    """Each activity draws its check-in rate (share of confirmed registrations) and its
    certificate rate (share of check-ins) uniformly from these ranges."""

    check_in: tuple[float, float] = MIXED_CHECK_IN_RATES
    certificate: tuple[float, float] = MIXED_CERTIFICATE_RATES
    cancel: float = MIXED_CANCEL_RATE
    offer_lapse: float = MIXED_OFFER_LAPSE_RATE


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


def _percent_range(
    environ: Mapping[str, str], prefix: str, default: tuple[float, float]
) -> tuple[float, float]:
    bounds = []
    for suffix, fallback in zip(("MIN", "MAX"), default):
        key = f"{prefix}_{suffix}_PERCENT"
        raw = environ.get(key, "").strip()
        if not raw:
            bounds.append(fallback)
            continue
        if not raw.isdigit() or int(raw) > 100:
            raise ScenarioConfigError(f"{key} must be an integer between 0 and 100.")
        bounds.append(int(raw) / 100)
    if bounds[0] > bounds[1]:
        raise ScenarioConfigError(
            f"{prefix}_MIN_PERCENT must not be greater than {prefix}_MAX_PERCENT."
        )
    return bounds[0], bounds[1]


def _percent(environ: Mapping[str, str], key: str, default: float) -> float:
    raw = environ.get(key, "").strip()
    if not raw:
        return default
    if not raw.isdigit() or int(raw) > 100:
        raise ScenarioConfigError(f"{key} must be an integer between 0 and 100.")
    return int(raw) / 100


def mixed_rates(environ: Mapping[str, str]) -> MixedRates:
    """Mixed-load behaviour rates, from percentages."""
    return MixedRates(
        _percent_range(environ, "SCENARIO_CHECK_IN", MIXED_CHECK_IN_RATES),
        _percent_range(environ, "SCENARIO_CERTIFICATE", MIXED_CERTIFICATE_RATES),
        _percent(environ, "SCENARIO_CANCEL_PERCENT", MIXED_CANCEL_RATE),
        _percent(environ, "SCENARIO_OFFER_LAPSE_PERCENT", MIXED_OFFER_LAPSE_RATE),
    )


def plan_mixed_load(
    participants: int,
    duration_seconds: int,
    rng: random.Random,
    rates: MixedRates | None = None,
) -> MixedPlan:
    """Random registrations, cancellations, check-ins and certificates over 3 x 3 activities.

    Seats (2-5 per activity for 20 participants) scale with the participant count.
    Activities start and end at staggered moments within the run. Registrations
    and cancellations happen before an activity starts, check-ins while it runs
    (by default 40-90% of the seats kept, drawn per activity) and certificates
    after it ends (by default 50-100% of the check-ins, drawn per activity).
    """
    rates = rates or MixedRates()
    scale = participants / MIXED_BASE_PARTICIPANTS
    activities = []
    for event_index, (_, titles) in enumerate(MIXED_EVENTS):
        paid_index = rng.randrange(len(titles))
        for index, title in enumerate(titles):
            starts_at = duration_seconds * (
                MIXED_FIRST_START + event_index * MIXED_EVENT_STEP + index * MIXED_ACTIVITY_STEP
            )
            activities.append(
                MixedActivity(
                    event_index,
                    title,
                    capacity=max(1, round(rng.randint(2, 5) * scale)),
                    price_minor=rng.choice(MIXED_PRICES_MINOR) if index == paid_index else 0,
                    starts_at_seconds=round(starts_at, 1),
                    ends_at_seconds=round(starts_at + duration_seconds * MIXED_ACTIVITY_LENGTH, 1),
                    check_in_rate=round(rng.uniform(*rates.check_in), 2),
                    certificate_rate=round(rng.uniform(*rates.certificate), 2),
                )
            )
    popularity = [rng.randint(1, 5) for _ in activities]
    certificate_deadline = duration_seconds * MIXED_CERTIFICATE_DEADLINE
    actions = []
    for participant_index in range(participants):
        target = rng.randint(2, 4)
        chosen: list[int] = []
        while len(chosen) < target:
            pick = rng.choices(range(len(activities)), weights=popularity)[0]
            if pick not in chosen:
                chosen.append(pick)
        for activity_index in chosen:
            spec = activities[activity_index]
            closes = max(0.0, spec.starts_at_seconds - MIXED_ADMISSION_MARGIN_SECONDS)
            delay = round(rng.uniform(0, closes), 1)
            cancel_after = None
            cancel_room = closes - delay - MIXED_PROMOTION_SECONDS
            if spec.price_minor == 0 and cancel_room >= 1 and rng.random() < rates.cancel:
                cancel_after = round(rng.uniform(1, cancel_room), 1)
            check_in_at = certificate_at = None
            if cancel_after is None and rng.random() < spec.check_in_rate:
                check_in_at = round(rng.uniform(spec.starts_at_seconds, spec.ends_at_seconds), 1)
                if rng.random() < spec.certificate_rate:
                    certificate_at = round(
                        rng.uniform(
                            spec.ends_at_seconds + 1,
                            max(spec.ends_at_seconds + 1, certificate_deadline),
                        ),
                        1,
                    )
            actions.append(
                MixedAction(
                    participant_index,
                    activity_index,
                    delay,
                    cancel_after,
                    check_in_at,
                    certificate_at,
                    ignores_offer=rng.random() < rates.offer_lapse,
                )
            )
    actions.sort(key=lambda item: item.delay_seconds)
    return MixedPlan(tuple(activities), tuple(actions))


async def _prepare_mixed_events(
    clients, plan: MixedPlan, organizer, run_id: UUID, now: datetime, duration: int
):
    """Create the events with parked activities; return (activity, input) pairs."""
    stamp = now.strftime("%H:%M:%S")
    origin = now + timedelta(seconds=MIXED_PREPARATION_SECONDS)
    parked = origin + timedelta(seconds=MIXED_PARKED_SECONDS)
    created = []
    for event_index, (event_title, _) in enumerate(MIXED_EVENTS):
        event = await clients.create_event(
            EventInput(
                organizationId=UUID(organizer["organizationId"]),
                title=f"[CENÁRIO] Carga mista · {event_title} · {stamp}",
                description="Evento fictício do GESAD/UECE gerado pelo script de cenário "
                "(carga mista: inscrições, desistências, lista de espera, check-ins e "
                "certificados, tudo em poucos segundos).",
                startsAt=origin,
                endsAt=parked + timedelta(seconds=duration + 60),
            ),
            token=organizer["token"],
            idempotency_key=f"scenario-{run_id}-event-{event_index}",
        )
        for activity_index, spec in enumerate(plan.activities):
            if spec.event_index != event_index:
                continue
            activity_input = ActivityInput(
                title=f"[CENÁRIO] {spec.title}",
                startsAt=parked + timedelta(seconds=spec.starts_at_seconds),
                endsAt=parked + timedelta(seconds=spec.ends_at_seconds),
                location="Laboratório GESAD/UECE",
                responsible="Equipe GESAD/UECE",
                capacity=spec.capacity,
                price=Money(amountMinor=spec.price_minor, currency="BRL"),
            )
            activity = await clients.create_activity(
                event.id,
                activity_input,
                token=organizer["token"],
                idempotency_key=f"scenario-{run_id}-activity-{activity_index}",
            )
            created.append((activity, activity_input))
        await clients.publish_event(
            event.id,
            token=organizer["token"],
            idempotency_key=f"scenario-{run_id}-publish-{event_index}",
        )
    return created


def _percent_text(bounds: tuple[float, float]) -> str:
    low, high = (round(bound * 100) for bound in bounds)
    return f"{low}%" if low == high else f"{low}–{high}%"


async def _run_mixed_load(
    clients,
    organizer: Mapping[str, str],
    identities: Sequence[Mapping[str, str]],
    *,
    participants: int,
    duration: int,
    out: Callable[[str], None],
    sleep: Callable[[float], Awaitable[None]],
    now: Callable[[], datetime],
    rng: random.Random,
    rates: MixedRates | None = None,
    clock: Callable[[], float] = monotonic,
) -> bool:
    identities = identities[:participants]
    rates = rates or MixedRates()
    plan = plan_mixed_load(len(identities), duration, rng, rates)
    run_id = uuid4()
    paid = sum(spec.price_minor > 0 for spec in plan.activities)
    out("Cenário: Carga mista")
    out(
        f"{len(MIXED_EVENTS)} eventos · {len(plan.activities)} atividades ({paid} pagas) · "
        f"{len(plan.actions)} inscrições aleatórias em {duration} s"
    )
    out(
        "Cada atividade começa e termina durante o cenário: o organizador a abre depois das "
        "inscrições e desistências; check-ins durante, certificados depois."
    )
    out(
        f"Check-in por atividade: {_percent_text(rates.check_in)} das inscrições confirmadas · "
        f"certificado: {_percent_text(rates.certificate)} dos check-ins"
    )
    out(
        f"Desistência: {_percent_text((rates.cancel, rates.cancel))} das inscrições gratuitas · "
        f"ofertas ignoradas: {_percent_text((rates.offer_lapse, rates.offer_lapse))}"
    )
    preparing = clock()
    try:
        prepared = await _prepare_mixed_events(
            clients, plan, organizer, run_id, now(), duration
        )
    except SERVICE_ERRORS as error:
        out(f"Não foi possível preparar os eventos: {_error_code(error)}")
        return False
    activities = [activity for activity, _ in prepared]
    out("Eventos publicados: [CENÁRIO] Carga mista · …")
    out("")
    await sleep(max(0.0, MIXED_PREPARATION_SECONDS - (clock() - preparing)))

    started = clock()
    statuses: Counter[str] = Counter()
    errors: Counter[str] = Counter()
    cancelled = 0
    promoted = 0
    lapsed = 0
    late_offers = 0
    refused = 0
    checked_in = 0
    certificates = 0
    confirmed = 0
    payments_pending = 0
    waiting: dict[int, list[tuple[UUID, Mapping[str, str], str, MixedAction]]] = {}
    pending = Counter(action.activity_index for action in plan.actions)
    ready = [asyncio.Event() for _ in plan.activities]
    opened = [asyncio.Event() for _ in plan.activities]
    # Seconds each activity started after its planned moment; None if it never opened.
    shifts: list[float | None] = [None] * len(plan.activities)
    for activity_index in range(len(plan.activities)):
        if not pending[activity_index]:
            ready[activity_index].set()

    def log(participant: str, activity_index: int, text: str) -> None:
        title = plan.activities[activity_index].title
        out(f"[{clock() - started:5.1f}s] {participant} · {title} → {text}")

    async def wait_until(seconds: float) -> None:
        await sleep(max(0.0, seconds - (clock() - started)))

    def phase_done(activity_index: int) -> None:
        pending[activity_index] -= 1
        if not pending[activity_index]:
            ready[activity_index].set()

    async def open_activity(activity_index: int) -> None:
        spec = plan.activities[activity_index]
        activity, activity_input = prepared[activity_index]
        await ready[activity_index].wait()
        await wait_until(spec.starts_at_seconds)
        length = spec.ends_at_seconds - spec.starts_at_seconds
        starts_at = now() + timedelta(seconds=1)
        try:
            await clients.update_activity(
                activity.id,
                activity_input.model_copy(
                    update={
                        "starts_at": starts_at,
                        "ends_at": starts_at + timedelta(seconds=length),
                    }
                ),
                token=organizer["token"],
                idempotency_key=f"scenario-{run_id}-open-{activity_index}",
            )
        except SERVICE_ERRORS as error:
            errors[_error_code(error)] += 1
            out(f"[{clock() - started:5.1f}s] Organizador · {spec.title} → erro "
                f"{_error_code(error)}")
        else:
            shifts[activity_index] = clock() - started + 1 - spec.starts_at_seconds
            out(f"[{clock() - started:5.1f}s] Organizador · {spec.title} → atividade "
                f"começou (dura {length:.0f} s)")
        opened[activity_index].set()

    async def with_retries(call, retryable, participant: str, action: MixedAction, what: str):
        for attempt in range(RETRY_ATTEMPTS):
            try:
                return await call()
            except SERVICE_ERRORS as error:
                code = _error_code(error)
                if code in retryable and attempt < RETRY_ATTEMPTS - 1:
                    await sleep(1)
                    continue
                errors[code] += 1
                log(participant, action.activity_index, f"erro {what} {code}")
                return None
        return None

    async def attend(
        registration_id: UUID, identity: Mapping[str, str], participant: str, action: MixedAction
    ) -> None:
        nonlocal checked_in, certificates
        if action.check_in_at_seconds is None:
            return
        await opened[action.activity_index].wait()
        shift = shifts[action.activity_index]
        if shift is None:
            return
        await wait_until(action.check_in_at_seconds + shift)
        recorded = await with_retries(
            lambda: clients.check_in(
                registration_id,
                token=identity["token"],
                idempotency_key=f"scenario-{run_id}-check-in-{registration_id}",
                correlation_id=uuid4(),
            ),
            CHECK_IN_RETRYABLE,
            participant,
            action,
            "no check-in",
        )
        if recorded is None:
            return
        checked_in += 1
        log(participant, action.activity_index, "check-in registrado")
        if action.certificate_at_seconds is None:
            return
        await wait_until(action.certificate_at_seconds + shift)
        issued = await with_retries(
            lambda: clients.issue_certificate(
                registration_id,
                token=identity["token"],
                idempotency_key=f"scenario-{run_id}-certificate-{registration_id}",
                correlation_id=uuid4(),
            ),
            CERTIFICATE_RETRYABLE,
            participant,
            action,
            "no certificado",
        )
        if issued is not None:
            certificates += 1
            log(participant, action.activity_index, "certificado emitido")

    async def await_payment(
        registration_id: UUID, identity: Mapping[str, str], participant: str, action: MixedAction
    ) -> bool:
        nonlocal confirmed, payments_pending, refused
        for _ in range(PAYMENT_CONFIRMATION_ATTEMPTS):
            await sleep(1)
            try:
                current = await clients.get_registration(registration_id, token=identity["token"])
            except SERVICE_ERRORS as error:
                errors[_error_code(error)] += 1
                log(participant, action.activity_index, f"erro {_error_code(error)}")
                return False
            if current.status == "CONFIRMED":
                confirmed += 1
                log(participant, action.activity_index, "pagamento aprovado → CONFIRMED")
                return True
            if current.status != "PENDING_PAYMENT":
                refused += current.status == "REFUSED"
                log(participant, action.activity_index, current.status)
                return False
        payments_pending += 1
        return False

    Attendee = tuple[UUID, Mapping[str, str], str, MixedAction]

    async def promote(activity_index: int) -> Attendee | None:
        nonlocal promoted, confirmed, lapsed, late_offers
        for _ in range(3):
            await sleep(1)
            for entry in list(waiting.get(activity_index, ())):
                registration_id, identity, participant, waiting_action = entry
                try:
                    current = await clients.get_registration(
                        registration_id, token=identity["token"]
                    )
                    if current.status != "OFFERED" or entry not in waiting[activity_index]:
                        continue
                    # Claim the offer so a concurrent promotion skips it.
                    waiting[activity_index].remove(entry)
                    if waiting_action.ignores_offer:
                        lapsed += 1
                        log(participant, activity_index, "ignorou a oferta (vai expirar)")
                        return None
                    accepted = await clients.accept_offer(
                        registration_id,
                        token=identity["token"],
                        idempotency_key=f"scenario-{run_id}-accept-{registration_id}",
                    )
                except SERVICE_ERRORS as error:
                    code = _error_code(error)
                    # Under heavy load the acceptance may arrive after the offer deadline;
                    # Registration then expires it (INVALID_STATE if its worker got there
                    # first), which is the expected business outcome.
                    if code in LATE_OFFER_CODES:
                        late_offers += 1
                        log(participant, activity_index, "oferta expirou antes do aceite")
                        return None
                    errors[code] += 1
                    log(participant, activity_index, f"erro {code}")
                    return None
                promoted += 1
                log(participant, activity_index, f"saiu da lista de espera → {accepted.status}")
                if accepted.status != "CONFIRMED":
                    return None
                confirmed += 1
                return registration_id, identity, participant, waiting_action
        return None

    async def act(index: int, action: MixedAction) -> None:
        try:
            attendee = await register(index, action)
        finally:
            phase_done(action.activity_index)
        if attendee is not None:
            await attend(*attendee)

    async def register(index: int, action: MixedAction) -> Attendee | None:
        """Everything that must happen before the activity starts."""
        nonlocal cancelled, confirmed
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
            return None
        statuses[registration.status] += 1
        log(participant, action.activity_index, registration.status)
        if registration.status == "WAITLISTED":
            waiting.setdefault(action.activity_index, []).append(
                (registration.id, identity, participant, action)
            )
        if registration.status == "PENDING_PAYMENT":
            if await await_payment(registration.id, identity, participant, action):
                return registration.id, identity, participant, action
            return None
        if registration.status != "CONFIRMED":
            return None
        confirmed += 1
        if action.cancel_after_seconds is None:
            return registration.id, identity, participant, action
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
            return None
        cancelled += 1
        confirmed -= 1
        log(participant, action.activity_index, "desistiu (CANCELLED) · vaga liberada")
        return await promote(action.activity_index)

    await asyncio.gather(
        *(act(index, action) for index, action in enumerate(plan.actions)),
        *(open_activity(index) for index in range(len(plan.activities))),
    )

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
    out(f"Ofertas da lista de espera ignoradas (expiram depois): {lapsed}")
    if late_offers:
        out(f"Ofertas que expiraram antes do aceite (carga alta): {late_offers}")
    out(f"Pagamentos recusados: {refused}")
    out(f"Check-ins: {checked_in} de {confirmed} inscrições confirmadas")
    out(f"Certificados emitidos: {certificates} de {checked_in} check-ins")
    if payments_pending:
        out(f"Pagamentos ainda não confirmados ao fim do cenário: {payments_pending}")
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
    clients_factory: Callable[[Settings], DemoClients] = scenario_clients,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    rng: random.Random | None = None,
) -> int:
    """Run every scenario in parallel, plus a random mixed load."""
    try:
        settings, organizer, identities, participants, _ = _load_configuration(environ)
        duration = mixed_duration(environ)
        rates = mixed_rates(environ)
    except (SettingsError, ScenarioConfigError, OSError, KeyError, ValueError) as error:
        out(f"Erro de configuração: {error}")
        return 2

    width = max(len(key) for key in PARALLEL_KEYS)

    def prefixed(key: str) -> Callable[[str], None]:
        return lambda line: line and out(f"[{key:<{width}}] {line}")

    out(f"Todos os cenários em paralelo, durante cerca de {duration} s")
    out("Acompanhe em Relatórios, entrando como Organizador — GESAD/UECE.")
    out("")
    base_clients = clients_factory(settings)
    metrics = None
    if environ.get(METRICS_ENV, "").strip().lower() in {"1", "true"}:
        metrics = RequestMetrics()
        base_clients.observer = metrics.record
    clients = ResendingClients(base_clients, sleep)
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
            participants=participants,
            duration=duration,
            out=prefixed(MIXED_KEY),
            sleep=sleep,
            now=now,
            rng=rng or random.Random(),
            rates=rates,
        )
        results = await asyncio.gather(*single, mixed)
    finally:
        await clients.aclose()

    out("")
    _report_resends(clients, out)
    out("Resumo")
    for key, passed in zip(PARALLEL_KEYS, results):
        out(f"  {key:<{width}} {'OK' if passed else 'VERIFICAR'}")
    out("Veredito geral: OK" if all(results) else "Veredito geral: VERIFICAR")
    if metrics is not None:
        out("METRICS " + json.dumps(metrics.summary(clients.resent), separators=(",", ":")))
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
