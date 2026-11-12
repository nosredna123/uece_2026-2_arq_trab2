import asyncio
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from demo_web.errors import ServiceError, TransportError
from demo_web.models import (
    Activity,
    Availability,
    Certificate,
    CheckIn,
    Event,
    Registration,
)
from demo_web.scenarios import (
    SCENARIOS,
    MixedAction,
    MixedActivity,
    MixedPlan,
    Outcome,
    ScenarioConfigError,
    evaluate,
    main,
    mixed_duration,
    peak_capacity,
    peak_settings,
    plan_mixed_load,
    mixed_rates,
    MixedRates,
    plan_submissions,
    run_all,
    run_scenario,
    scenario_clients,
)
from demo_web import scenarios
from demo_web.clients import DemoClients, RequestHistoryEntry
from demo_web.settings import Settings


ROOT = Path(__file__).resolve().parents[3]
SCENARIO_FIXTURES = ROOT / "mocks/identity-access/scenario-participants.json"
ORGANIZATION_ID = UUID("30000000-0000-4000-8000-000000000001")
NOW = datetime(2030, 3, 1, 12, 0, tzinfo=timezone.utc)
IDENTITIES = [{"token": f"token-{index:02}"} for index in range(1, 21)]


def _environment(web_environment, **overrides):
    return {
        **web_environment,
        "IDENTITY_LAB_ENABLED": "true",
        "DEMO_LAB_ENABLED": "true",
        "DEMO_LAB_FIXTURES_PATH": str(SCENARIO_FIXTURES),
        "DEMO_LAB_MAX_PARTICIPANTS": "50",
        "DEMO_LAB_MAX_CONCURRENCY": "10",
        "DEMO_LAB_RUN_TIMEOUT_SECONDS": "60",
        "DEMO_LAB_SETTLE_TIMEOUT_SECONDS": "30",
        "DEMO_LAB_RUN_TTL_SECONDS": "1800",
        "DEMO_LAB_MAX_RETAINED_RUNS": "5",
        **overrides,
    }


class FakeRegistration:
    """In-memory Registration service honouring capacity and idempotency keys."""

    def __init__(self, *, overbook=False):
        self.capacity = 0
        self.by_key = {}
        self.confirmed = 0
        self.overbook = overbook
        self.event = None
        self.activity = None
        self.calls = []
        self.closed = False

    async def create_event(self, event_input, *, token, idempotency_key):
        self.calls.append(("create_event", token, idempotency_key))
        self.event = Event(
            id=uuid4(),
            organizationId=event_input.organization_id,
            title=event_input.title,
            description=event_input.description,
            startsAt=event_input.starts_at,
            endsAt=event_input.ends_at,
            status="DRAFT",
            version=1,
        )
        return self.event

    async def create_activity(self, event_id, activity_input, *, token, idempotency_key):
        self.calls.append(("create_activity", token, idempotency_key))
        self.capacity = activity_input.capacity
        self.activity = Activity(
            id=uuid4(),
            eventId=event_id,
            organizationId=ORGANIZATION_ID,
            title=activity_input.title,
            startsAt=activity_input.starts_at,
            endsAt=activity_input.ends_at,
            location=activity_input.location,
            responsible=activity_input.responsible,
            capacity=activity_input.capacity,
            price=activity_input.price,
            version=1,
            capacityFrozen=False,
        )
        return self.activity

    async def publish_event(self, event_id, *, token, idempotency_key):
        self.calls.append(("publish_event", token, idempotency_key))
        return self.event

    async def request_registration(self, activity_id, *, token, idempotency_key, correlation_id):
        await asyncio.sleep(0)
        if idempotency_key not in self.by_key:
            seat = self.overbook or self.confirmed < self.capacity
            self.confirmed += seat
            self.by_key[idempotency_key] = Registration(
                id=uuid4(),
                activityId=activity_id,
                participantId=uuid4(),
                organizationId=ORGANIZATION_ID,
                status="CONFIRMED" if seat else "WAITLISTED",
                refundStatus="NONE",
                createdAt=NOW,
                version=1,
            )
        return self.by_key[idempotency_key]

    async def get_availability(self, activity_id):
        return Availability(
            activityId=activity_id,
            capacity=self.capacity,
            occupied=self.confirmed,
            available=max(0, self.capacity - self.confirmed),
            asOf=NOW,
        )

    async def aclose(self):
        self.closed = True


async def _no_sleep(_seconds):
    await asyncio.sleep(0)


def _run(web_environment, scenario, fake, **overrides):
    lines = []
    code = asyncio.run(
        run_scenario(
            scenario,
            environ=_environment(web_environment, **overrides),
            out=lines.append,
            clients_factory=lambda settings: fake,
            sleep=_no_sleep,
            now=lambda: NOW,
        )
    )
    return code, lines


def test_peak_settings_defaults_and_bounds():
    assert peak_settings({}, 20) == (20, 30)
    assert peak_settings({"SCENARIO_PEAK_PARTICIPANTS": "8"}, 20) == (8, 30)
    assert peak_settings({"SCENARIO_PEAK_DURATION_SECONDS": "60"}, 20) == (20, 60)
    assert peak_settings({}, 5) == (5, 30)
    assert peak_settings({"SCENARIO_PEAK_PARTICIPANTS": "20000"}, 20000) == (20000, 30)
    for key, value in (
        ("SCENARIO_PEAK_PARTICIPANTS", "0"),
        ("SCENARIO_PEAK_PARTICIPANTS", "21"),
        ("SCENARIO_PEAK_PARTICIPANTS", "abc"),
        ("SCENARIO_PEAK_DURATION_SECONDS", "301"),
    ):
        with pytest.raises(ScenarioConfigError):
            peak_settings({key: value}, 20)


def test_peak_capacity_is_a_quarter_of_the_participants():
    assert peak_capacity(1) == 1
    assert peak_capacity(3) == 1
    assert peak_capacity(20) == 5
    assert peak_capacity(21) == 6
    assert peak_capacity(200) == 50


def test_peak_plan_spreads_submissions_over_the_duration():
    run_id = uuid4()
    plan = plan_submissions(
        SCENARIOS["pico-inscricoes"],
        IDENTITIES,
        run_id,
        participants=10,
        duration_seconds=30,
    )
    assert len(plan) == 10
    assert [item.delay_seconds for item in plan] == [index * 3.0 for index in range(10)]
    assert len({item.idempotency_key for item in plan}) == 10
    assert len({item.token for item in plan}) == 10


def test_last_seat_and_duplicate_plans():
    run_id = uuid4()
    last_seat = plan_submissions(SCENARIOS["ultima-vaga"], IDENTITIES, run_id)
    assert len(last_seat) == 2 and {item.delay_seconds for item in last_seat} == {0.0}
    assert last_seat[0].token != last_seat[1].token

    duplicate = plan_submissions(SCENARIOS["envios-repetidos"], IDENTITIES, run_id)
    assert len(duplicate) == 10
    assert len({item.idempotency_key for item in duplicate}) == 1
    assert len({item.token for item in duplicate}) == 1


def test_registration_peak_run_prints_progress_and_verdict(web_environment):
    fake = FakeRegistration()
    code, lines = _run(
        web_environment,
        "pico-inscricoes",
        fake,
        SCENARIO_PEAK_PARTICIPANTS="12",
        SCENARIO_PEAK_DURATION_SECONDS="6",
    )

    assert code == 0
    assert fake.closed
    assert [call[0] for call in fake.calls] == ["create_event", "create_activity", "publish_event"]
    assert fake.event.title.startswith("[CENÁRIO] Pico de inscrições")
    assert fake.capacity == 3
    progress = [line for line in lines if "→" in line]
    assert len(progress) == 12
    assert sum("CONFIRMED" in line for line in progress) == 3
    assert sum("WAITLISTED" in line for line in progress) == 9
    assert "12 participantes em 6 s · 3 vagas" in lines
    assert "Vagas ocupadas: 3 de 3 — sem overbooking." in lines
    assert lines[-1] == "Veredito: OK"


def test_last_seat_and_duplicate_runs_pass(web_environment):
    code, lines = _run(web_environment, "ultima-vaga", FakeRegistration())
    assert code == 0
    assert "Resultado: 1 CONFIRMED, 1 WAITLISTED" in lines

    code, lines = _run(web_environment, "envios-repetidos", FakeRegistration())
    assert code == 0
    assert "Resultado: 10 CONFIRMED" in lines
    assert any(line.startswith("Inscrições distintas criadas: 1 ") for line in lines)


def test_overbooking_is_reported(web_environment):
    code, lines = _run(web_environment, "ultima-vaga", FakeRegistration(overbook=True))
    assert code == 1
    assert "Vagas ocupadas: 2 de 1 — OVERBOOKING!" in lines
    assert lines[-1] == "Veredito: VERIFICAR"


def test_errors_are_counted_and_fail_the_verdict():
    outcomes = [
        Outcome("Participante de cenário 01", 0.0, status="CONFIRMED", registration_id=uuid4()),
        Outcome("Participante de cenário 02", 0.1, error_code="WAITLIST_DISABLED"),
    ]
    availability = Availability(
        activityId=uuid4(), capacity=1, occupied=1, available=0, asOf=NOW
    )
    verdict = evaluate(SCENARIOS["ultima-vaga"], outcomes, availability)
    assert not verdict.passed
    assert "Erros: 1 WAITLIST_DISABLED" in verdict.lines


def test_preparation_failure_is_reported(web_environment):
    class Failing(FakeRegistration):
        async def create_event(self, *args, **kwargs):
            raise ServiceError(
                service="event-management",
                method="POST",
                status_code=503,
                code="UNAVAILABLE",
                message="down",
                correlation_id=uuid4(),
            )

    fake = Failing()
    code, lines = _run(web_environment, "pico-inscricoes", fake)
    assert code == 1
    assert "Não foi possível preparar o evento: UNAVAILABLE" in lines
    assert fake.closed


def test_scenarios_require_the_scenario_identity_pool(web_environment):
    lines = []
    code = asyncio.run(
        run_scenario(
            "ultima-vaga",
            environ=web_environment,
            out=lines.append,
            clients_factory=lambda settings: pytest.fail("no clients expected"),
        )
    )
    assert code == 2
    assert lines[0].startswith("Erro de configuração")


def test_invalid_peak_configuration_is_reported(web_environment):
    code, lines = _run(
        web_environment,
        "pico-inscricoes",
        FakeRegistration(),
        SCENARIO_PEAK_PARTICIPANTS="20001",
    )
    assert code == 2
    assert "SCENARIO_PEAK_PARTICIPANTS" in lines[0]


def test_peak_accepts_more_than_twenty_participants(web_environment):
    fake = FakeRegistration()
    code, lines = _run(
        web_environment,
        "pico-inscricoes",
        fake,
        SCENARIO_PEAK_PARTICIPANTS="200",
        SCENARIO_PEAK_DURATION_SECONDS="1",
    )
    assert code == 0, lines
    assert "200 participantes em 1 s · 50 vagas" in lines
    assert "Resultado: 50 CONFIRMED, 150 WAITLISTED" in lines


def test_cli_rejects_unknown_scenarios():
    with pytest.raises(SystemExit) as raised:
        main(["nao-existe"])
    assert raised.value.code == 2


class FakeEventHub(FakeRegistration):
    """Several activities, paid seats, cancellation and waiting-list offers."""

    def __init__(self, *, overbook=False):
        super().__init__(overbook=overbook)
        self.activities = {}
        self.registrations = {}
        self.queues = {}
        self.check_ins = set()
        self.certificates = set()
        self.attendance_lag = set()
        self.opened = {}

    def _closed(self, activity_id):
        """Registration rejects admissions, cancellations and offers once an activity starts."""
        if activity_id in self.opened:
            raise ServiceError(
                service="registration",
                method="POST",
                status_code=409,
                code="ACTIVITY_INELIGIBLE",
                message="started",
                correlation_id=uuid4(),
            )

    async def update_activity(self, activity_id, activity_input, *, token, idempotency_key):
        await asyncio.sleep(0)
        assert activity_id not in self.opened
        updated = self.activities[activity_id].model_copy(
            update={"starts_at": activity_input.starts_at, "ends_at": activity_input.ends_at}
        )
        self.activities[activity_id] = updated
        self.opened[activity_id] = (activity_input.starts_at, activity_input.ends_at)
        return updated

    async def create_activity(self, event_id, activity_input, *, token, idempotency_key):
        activity = await super().create_activity(
            event_id, activity_input, token=token, idempotency_key=idempotency_key
        )
        self.activities[activity.id] = activity
        self.queues[activity.id] = []
        return activity

    def _occupied(self, activity_id):
        return sum(
            registration.status in {"CONFIRMED", "PENDING_PAYMENT", "OFFERED"}
            for registration in self.registrations.values()
            if registration.activity_id == activity_id
        )

    def _store(self, registration, status):
        updated = registration.model_copy(update={"status": status})
        self.registrations[registration.id] = updated
        return updated

    async def request_registration(self, activity_id, *, token, idempotency_key, correlation_id):
        await asyncio.sleep(0)
        if idempotency_key in self.by_key:
            return self.registrations[self.by_key[idempotency_key]]
        self._closed(activity_id)
        activity = self.activities[activity_id]
        seat = self.overbook or self._occupied(activity_id) < activity.capacity
        paid = activity.price.amount_minor > 0
        status = ("PENDING_PAYMENT" if paid else "CONFIRMED") if seat else "WAITLISTED"
        registration = Registration(
            id=uuid4(),
            activityId=activity_id,
            participantId=uuid4(),
            organizationId=ORGANIZATION_ID,
            status=status,
            refundStatus="NONE",
            createdAt=NOW,
            version=1,
        )
        self.registrations[registration.id] = registration
        self.by_key[idempotency_key] = registration.id
        if status == "WAITLISTED":
            self.queues[activity_id].append(registration.id)
        return registration

    async def cancel_registration(self, registration_id, *, token, idempotency_key):
        self._closed(self.registrations[registration_id].activity_id)
        registration = self._store(self.registrations[registration_id], "CANCELLED")
        queue = self.queues[registration.activity_id]
        if queue:
            self._store(self.registrations[queue.pop(0)], "OFFERED")
        return registration

    async def get_registration(self, registration_id, *, token):
        await asyncio.sleep(0)
        registration = self.registrations[registration_id]
        if registration.status == "PENDING_PAYMENT":
            return self._store(registration, "CONFIRMED")
        return registration

    def _attendance_error(self, code):
        return ServiceError(
            service="attendance",
            method="POST",
            status_code=409 if code == "ELIGIBILITY_NOT_READY" else 422,
            code=code,
            message=code,
            correlation_id=uuid4(),
        )

    async def check_in(self, registration_id, *, token, idempotency_key, correlation_id):
        await asyncio.sleep(0)
        if self.registrations[registration_id].activity_id not in self.opened:
            raise self._attendance_error("ACTIVITY_NOT_OPEN")
        if registration_id not in self.attendance_lag:
            self.attendance_lag.add(registration_id)
            raise self._attendance_error("ELIGIBILITY_NOT_READY")
        if self.registrations[registration_id].status != "CONFIRMED":
            raise self._attendance_error("REGISTRATION_NOT_CONFIRMED")
        self.check_ins.add(registration_id)
        return CheckIn(
            registrationId=registration_id, participantId=uuid4(), checkedInAt=NOW
        )

    async def issue_certificate(self, registration_id, *, token, idempotency_key, correlation_id):
        await asyncio.sleep(0)
        if registration_id not in self.check_ins:
            raise self._attendance_error("CHECK_IN_REQUIRED")
        self.certificates.add(registration_id)
        registration = self.registrations[registration_id]
        return Certificate(
            id=uuid4(),
            registrationId=registration_id,
            participantId=uuid4(),
            activityId=registration.activity_id,
            issuedAt=NOW,
        )

    async def accept_offer(self, registration_id, *, token, idempotency_key):
        await asyncio.sleep(0)
        self._closed(self.registrations[registration_id].activity_id)
        return self._store(self.registrations[registration_id], "CONFIRMED")

    async def get_availability(self, activity_id):
        capacity = self.activities[activity_id].capacity
        occupied = self._occupied(activity_id)
        return Availability(
            activityId=activity_id,
            capacity=capacity,
            occupied=occupied,
            available=max(0, capacity - occupied),
            asOf=NOW,
        )


def _run_all(web_environment, fake, seed=7, **overrides):
    lines = []
    code = asyncio.run(
        run_all(
            environ=_environment(web_environment, **overrides),
            out=lines.append,
            clients_factory=lambda settings: fake,
            sleep=_no_sleep,
            now=lambda: NOW,
            rng=random.Random(seed),
        )
    )
    return code, lines


def test_mixed_duration_defaults_and_bounds():
    assert mixed_duration({}) == 60
    assert mixed_duration({"SCENARIO_MIXED_DURATION_SECONDS": "120"}) == 120
    for value in ("9", "301", "abc"):
        with pytest.raises(ScenarioConfigError):
            mixed_duration({"SCENARIO_MIXED_DURATION_SECONDS": value})


def test_mixed_plan_spreads_load_over_nine_activities():
    plan = plan_mixed_load(20, 60, random.Random(3))
    assert len(plan.activities) == 9
    assert sum(spec.price_minor > 0 for spec in plan.activities) == 3
    assert all(2 <= spec.capacity <= 5 for spec in plan.activities)
    assert 40 <= len(plan.actions) <= 80
    assert {action.participant_index for action in plan.actions} == set(range(20))
    pairs = [(action.participant_index, action.activity_index) for action in plan.actions]
    assert len(pairs) == len(set(pairs))
    cancelling = [action for action in plan.actions if action.cancel_after_seconds is not None]
    assert cancelling
    assert all(plan.activities[action.activity_index].price_minor == 0 for action in cancelling)
    assert all(0.4 <= spec.check_in_rate <= 0.9 for spec in plan.activities)
    assert all(0.5 <= spec.certificate_rate <= 1.0 for spec in plan.activities)
    assert len({spec.starts_at_seconds for spec in plan.activities}) == 9
    assert any(action.check_in_at_seconds is not None for action in plan.actions)
    assert any(action.certificate_at_seconds is not None for action in plan.actions)
    assert plan_mixed_load(20, 60, random.Random(3)) == plan


@pytest.mark.parametrize("duration", [10, 60, 300])
def test_mixed_plan_follows_each_activity_timeline(duration):
    plan = plan_mixed_load(200, duration, random.Random(5))
    for action in plan.actions:
        spec = plan.activities[action.activity_index]
        assert 0 < spec.starts_at_seconds < spec.ends_at_seconds < duration
        assert action.delay_seconds <= spec.starts_at_seconds - 3 + 1e-9
        if action.cancel_after_seconds is not None:
            # The freed seat must be offered and accepted before the activity starts.
            finished = action.delay_seconds + action.cancel_after_seconds + 4
            assert finished <= spec.starts_at_seconds - 3 + 1e-9
            assert action.check_in_at_seconds is None
        if action.check_in_at_seconds is not None:
            assert spec.starts_at_seconds <= action.check_in_at_seconds <= spec.ends_at_seconds
        if action.certificate_at_seconds is not None:
            assert action.check_in_at_seconds is not None
            assert spec.ends_at_seconds < action.certificate_at_seconds <= duration


def test_mixed_rates_default_and_read_percentages():
    assert mixed_rates({}) == MixedRates((0.4, 0.9), (0.5, 1.0))
    assert mixed_rates(
        {
            "SCENARIO_CHECK_IN_MIN_PERCENT": "0",
            "SCENARIO_CHECK_IN_MAX_PERCENT": "100",
            "SCENARIO_CERTIFICATE_MIN_PERCENT": "70",
            "SCENARIO_CERTIFICATE_MAX_PERCENT": "70",
        }
    ) == MixedRates((0.0, 1.0), (0.7, 0.7))
    assert mixed_rates({"SCENARIO_CHECK_IN_MAX_PERCENT": "60"}).check_in == (0.4, 0.6)


@pytest.mark.parametrize(
    "environ",
    [
        {"SCENARIO_CHECK_IN_MIN_PERCENT": "101"},
        {"SCENARIO_CERTIFICATE_MAX_PERCENT": "-1"},
        {"SCENARIO_CHECK_IN_MIN_PERCENT": "abc"},
        {"SCENARIO_CHECK_IN_MIN_PERCENT": "80", "SCENARIO_CHECK_IN_MAX_PERCENT": "50"},
        {"SCENARIO_CHECK_IN_MIN_PERCENT": "95"},
    ],
)
def test_mixed_rates_reject_invalid_percentages(environ):
    with pytest.raises(ScenarioConfigError):
        mixed_rates(environ)


def test_mixed_plan_uses_the_configured_rates():
    everyone = plan_mixed_load(200, 60, random.Random(3), MixedRates((1.0, 1.0), (1.0, 1.0)))
    assert all(spec.check_in_rate == spec.certificate_rate == 1.0 for spec in everyone.activities)
    for action in everyone.actions:
        if action.cancel_after_seconds is None:
            assert action.check_in_at_seconds is not None
            assert action.certificate_at_seconds is not None
    nobody = plan_mixed_load(200, 60, random.Random(3), MixedRates((0.0, 0.0), (0.5, 1.0)))
    assert all(action.check_in_at_seconds is None for action in nobody.actions)


def test_mixed_rates_read_cancellation_and_ignored_offer_percentages():
    defaults = mixed_rates({})
    assert (defaults.cancel, defaults.offer_lapse) == (0.2, 0.2)
    custom = mixed_rates({"SCENARIO_CANCEL_PERCENT": "0", "SCENARIO_OFFER_LAPSE_PERCENT": "100"})
    assert (custom.cancel, custom.offer_lapse) == (0.0, 1.0)
    for environ in ({"SCENARIO_CANCEL_PERCENT": "101"}, {"SCENARIO_OFFER_LAPSE_PERCENT": "x"}):
        with pytest.raises(ScenarioConfigError):
            mixed_rates(environ)


def test_mixed_plan_uses_the_cancellation_and_ignored_offer_rates():
    never = plan_mixed_load(200, 60, random.Random(3), MixedRates(cancel=0.0, offer_lapse=0.0))
    assert all(action.cancel_after_seconds is None for action in never.actions)
    assert not any(action.ignores_offer for action in never.actions)
    always = plan_mixed_load(200, 60, random.Random(3), MixedRates(cancel=1.0, offer_lapse=1.0))
    assert all(action.ignores_offer for action in always.actions)
    assert any(action.cancel_after_seconds is not None for action in always.actions)


def test_mixed_plan_scales_seats_with_the_participants():
    plan = plan_mixed_load(200, 60, random.Random(3))
    assert all(20 <= spec.capacity <= 50 for spec in plan.activities)
    assert {action.participant_index for action in plan.actions} == set(range(200))
    assert all(spec.capacity >= 1 for spec in plan_mixed_load(2, 60, random.Random(3)).activities)


def test_run_all_runs_every_scenario_in_parallel(web_environment):
    fake = FakeEventHub()
    code, lines = _run_all(web_environment, fake, SCENARIO_MIXED_DURATION_SECONDS="30")

    assert code == 0
    assert fake.closed
    assert lines[0] == "Todos os cenários em paralelo, durante cerca de 30 s"
    for key in ("pico-inscricoes", "ultima-vaga", "envios-repetidos", "carga-mista"):
        assert any(line.startswith(f"[{key:<16}] Veredito: OK") for line in lines)
        assert f"  {key:<16} OK" in lines
    assert len(fake.activities) == 3 + 9
    mixed = [line for line in lines if line.startswith("[carga-mista")]
    assert any("PENDING_PAYMENT" in line for line in mixed)
    assert any("desistiu (CANCELLED)" in line for line in mixed)
    assert any("saiu da lista de espera → CONFIRMED" in line for line in mixed)
    assert any("Atividades sem overbooking: 9 de 9." in line for line in mixed)
    assert any("pagamento aprovado → CONFIRMED" in line for line in mixed)
    assert fake.check_ins and fake.certificates
    assert fake.certificates <= fake.check_ins
    assert f"[carga-mista     ] Certificados emitidos: {len(fake.certificates)} de "
    f"{len(fake.check_ins)} check-ins" in lines
    assert sum("check-in registrado" in line for line in mixed) == len(fake.check_ins)
    assert lines[-1] == "Veredito geral: OK"


def test_run_all_prints_request_metrics_only_when_asked(web_environment):
    quiet = FakeEventHub()
    _, lines = _run_all(web_environment, quiet)
    assert not any(line.startswith("METRICS ") for line in lines)
    assert getattr(quiet, "observer", None) is None

    fake = FakeEventHub()
    code, lines = _run_all(web_environment, fake, SCENARIO_METRICS="1")

    assert code == 0
    assert lines[-2] == "Veredito geral: OK"
    metrics = json.loads(lines[-1].removeprefix("METRICS "))
    assert set(metrics) == {
        "durationSeconds",
        "requests",
        "requestsPerSecond",
        "failures",
        "resent",
        "latencyMs",
        "services",
    }
    assert callable(fake.observer)


def test_request_metrics_summarize_latency_and_failures():
    ticks = iter([100.0, 110.0])
    metrics = scenarios.RequestMetrics(clock=lambda: next(ticks))
    correlation = uuid4()
    for elapsed in range(1, 101):
        metrics.record(RequestHistoryEntry("registration", "POST", 201, elapsed, correlation))
    metrics.record(RequestHistoryEntry("registration", "POST", None, 5000, correlation))
    metrics.record(RequestHistoryEntry("attendance", "POST", 503, 7, correlation))
    metrics.record(RequestHistoryEntry("attendance", "POST", 409, 3, correlation))

    summary = metrics.summary(resent=2)

    assert summary["durationSeconds"] == 10.0
    assert summary["requests"] == 103
    assert summary["requestsPerSecond"] == 10.3
    assert summary["failures"] == 2
    assert summary["resent"] == 2
    assert summary["services"]["registration"]["latencyMs"] == {
        "p50": 51,
        "p95": 96,
        "p99": 100,
        "max": 5000,
    }
    assert summary["services"]["attendance"] == {
        "requests": 2,
        "failures": 1,
        "latencyMs": {"p50": 3, "p95": 7, "p99": 7, "max": 7},
    }
    assert scenarios.RequestMetrics().summary(0)["latencyMs"]["p50"] is None


def test_mixed_load_opens_each_activity_after_its_registrations(web_environment):
    fake = FakeEventHub()
    code, lines = _run_all(web_environment, fake, SCENARIO_MIXED_DURATION_SECONDS="60")

    assert code == 0
    mixed = {
        activity_id: activity
        for activity_id, activity in fake.activities.items()
        if "Carga mista" not in activity.title and activity_id in fake.opened
    }
    assert len(mixed) == 9
    for starts_at, ends_at in (fake.opened[activity_id] for activity_id in mixed):
        assert starts_at == NOW + timedelta(seconds=1)
        assert timedelta(seconds=10) <= ends_at - starts_at <= timedelta(seconds=11)
    assert sum("atividade começou (dura 11 s)" in line for line in lines) == 9


def test_activities_are_parked_until_opened(web_environment):
    class Recording(FakeEventHub):
        created = []

        async def create_activity(self, event_id, activity_input, **kwargs):
            self.created.append(activity_input.starts_at)
            return await super().create_activity(event_id, activity_input, **kwargs)

    fake = Recording()
    _run_all(web_environment, fake)
    assert all(
        starts_at >= NOW + timedelta(seconds=scenarios.MIXED_PARKED_SECONDS)
        for starts_at in fake.created[-9:]
    )


def test_unexpected_check_in_error_fails_the_mixed_verdict(web_environment):
    class Broken(FakeEventHub):
        async def check_in(self, registration_id, **kwargs):
            raise self._attendance_error("FORBIDDEN")

    code, lines = _run_all(web_environment, Broken())
    assert code == 1
    assert f"  {'carga-mista':<16} VERIFICAR" in lines
    assert any("erro no check-in FORBIDDEN" in line for line in lines)


class LateOffers(FakeEventHub):
    """Every acceptance arrives after the offer deadline (alternating error codes)."""

    def __init__(self, codes=("OFFER_EXPIRED", "INVALID_STATE")):
        super().__init__()
        self.codes = codes
        self.attempts = 0

    async def accept_offer(self, registration_id, *, token, idempotency_key):
        await asyncio.sleep(0)
        code = self.codes[self.attempts % len(self.codes)]
        self.attempts += 1
        self._store(self.registrations[registration_id], "EXPIRED")
        raise ServiceError(
            service="registration",
            method="POST",
            status_code=409,
            code=code,
            message=code,
            correlation_id=uuid4(),
        )


def test_offers_expired_before_acceptance_are_an_expected_outcome(web_environment):
    fake = LateOffers()
    code, lines = _run_all(web_environment, fake, SCENARIO_OFFER_LAPSE_PERCENT="0")

    assert fake.attempts >= 2
    mixed = [line for line in lines if line.startswith("[carga-mista")]
    assert sum("oferta expirou antes do aceite" in line for line in mixed) == fake.attempts
    assert f"[carga-mista     ] Ofertas que expiraram antes do aceite (carga alta): {fake.attempts}" in lines
    assert not any("Erros:" in line for line in mixed)
    assert code == 0 and lines[-1] == "Veredito geral: OK"


def test_other_offer_errors_still_fail_the_mixed_verdict(web_environment):
    code, lines = _run_all(web_environment, LateOffers(codes=("FORBIDDEN",)), SCENARIO_OFFER_LAPSE_PERCENT="0")
    assert code == 1
    assert any("Erros:" in line and "FORBIDDEN" in line for line in lines)


def test_run_all_scales_with_many_participants(web_environment):
    fake = FakeEventHub()
    code, lines = _run_all(
        web_environment, fake, seed=1, SCENARIO_PEAK_PARTICIPANTS="400"
    )

    assert code == 0, [line for line in lines if "erro" in line or "Veredito" in line]
    assert "[pico-inscricoes ] 400 participantes em 60 s · 100 vagas" in lines
    mixed = [line for line in lines if line.startswith("[carga-mista")]
    assert sum("saiu da lista de espera" in line for line in mixed) > 1
    assert lines[-1] == "Veredito geral: OK"


def test_simultaneous_cancellations_promote_distinct_waiting_participants(monkeypatch):
    plan = MixedPlan(
        (
            MixedActivity(
                0, "Oficina", capacity=2, price_minor=0, starts_at_seconds=20, ends_at_seconds=25
            ),
        ),
        tuple(
            MixedAction(index, 0, delay_seconds=0.0, cancel_after_seconds=cancel)
            for index, cancel in ((0, 1.0), (1, 1.0), (2, None), (3, None))
        ),
    )
    monkeypatch.setattr(scenarios, "plan_mixed_load", lambda *args: plan)
    fake = FakeEventHub()
    lines = []
    passed = asyncio.run(
        scenarios._run_mixed_load(
            fake,
            {"token": "organizer", "organizationId": str(ORGANIZATION_ID)},
            IDENTITIES,
            participants=4,
            duration=30,
            out=lines.append,
            sleep=_no_sleep,
            now=lambda: NOW,
            rng=random.Random(0),
        )
    )

    assert passed, lines
    assert "Desistências: 2 · vagas repassadas da lista de espera: 2" in lines
    promoted = [line for line in lines if "saiu da lista de espera" in line]
    assert {line.split(" · ")[0].split("] ")[1] for line in promoted} == {
        "Participante de cenário 03",
        "Participante de cenário 04",
    }


def test_timeouts_are_resent_with_the_same_idempotency_key(web_environment):
    class Flaky(FakeEventHub):
        keys = []

        async def request_registration(self, activity_id, *, idempotency_key, **kwargs):
            self.keys.append(idempotency_key)
            if self.keys.count(idempotency_key) == 1:
                raise TransportError(
                    service="registration",
                    method="POST",
                    code="HTTP_TIMEOUT",
                    message="timeout",
                    correlation_id=uuid4(),
                    outcome_unknown=True,
                )
            return await super().request_registration(
                activity_id, idempotency_key=idempotency_key, **kwargs
            )

    fake = Flaky()
    code, lines = _run_all(web_environment, fake)

    assert code == 0, [line for line in lines if "erro" in line]
    resent = len(set(fake.keys))
    assert all(fake.keys.count(key) >= 2 for key in fake.keys)
    assert f"Reenvios após timeout (mesma Idempotency-Key): {resent}" in lines


def test_persistent_timeouts_still_fail_the_scenario(web_environment):
    class Down(FakeRegistration):
        async def request_registration(self, *args, **kwargs):
            raise TransportError(
                service="registration",
                method="POST",
                code="HTTP_TIMEOUT",
                message="timeout",
                correlation_id=uuid4(),
                outcome_unknown=True,
            )

    code, lines = _run(web_environment, "ultima-vaga", Down())
    assert code == 1
    assert "Reenvios após timeout (mesma Idempotency-Key): 6" in lines


def test_scenario_clients_wait_for_a_free_connection(web_environment):
    settings = Settings.from_environment(_environment(web_environment))
    timeout = scenario_clients(settings)._client.timeout
    assert timeout.pool is None
    assert timeout.read == settings.http_timeout_seconds
    assert DemoClients(settings)._client.timeout.pool == settings.http_timeout_seconds


def test_run_all_reports_overbooking(web_environment):
    code, lines = _run_all(web_environment, FakeEventHub(overbook=True))
    assert code == 1
    assert f"  {'ultima-vaga':<16} VERIFICAR" in lines
    assert lines[-1] == "Veredito geral: VERIFICAR"


def test_run_all_rejects_invalid_mixed_duration(web_environment):
    code, lines = _run_all(
        web_environment, FakeEventHub(), SCENARIO_MIXED_DURATION_SECONDS="5"
    )
    assert code == 2
    assert "SCENARIO_MIXED_DURATION_SECONDS" in lines[0]
