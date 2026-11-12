import asyncio
import random
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from demo_web.errors import ServiceError
from demo_web.models import Activity, Availability, Event, Registration
from demo_web.scenarios import (
    SCENARIOS,
    Outcome,
    ScenarioConfigError,
    evaluate,
    main,
    mixed_duration,
    peak_settings,
    plan_mixed_load,
    plan_submissions,
    run_all,
    run_scenario,
)


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
    assert fake.capacity == 5
    progress = [line for line in lines if "→" in line]
    assert len(progress) == 12
    assert sum("CONFIRMED" in line for line in progress) == 5
    assert sum("WAITLISTED" in line for line in progress) == 7
    assert "12 participantes em 6 s · 5 vagas" in lines
    assert "Vagas ocupadas: 5 de 5 — sem overbooking." in lines
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
    assert "Resultado: 5 CONFIRMED, 195 WAITLISTED" in lines


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
        registration = self._store(self.registrations[registration_id], "CANCELLED")
        queue = self.queues[registration.activity_id]
        if queue:
            self._store(self.registrations[queue.pop(0)], "OFFERED")
        return registration

    async def get_registration(self, registration_id, *, token):
        return self.registrations[registration_id]

    async def accept_offer(self, registration_id, *, token, idempotency_key):
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
    assert all(0 <= action.delay_seconds <= 42 for action in plan.actions)
    cancelling = [action for action in plan.actions if action.cancel_after_seconds is not None]
    assert cancelling
    assert all(plan.activities[action.activity_index].price_minor == 0 for action in cancelling)
    assert plan_mixed_load(20, 60, random.Random(3)) == plan


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
    assert lines[-1] == "Veredito geral: OK"


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
