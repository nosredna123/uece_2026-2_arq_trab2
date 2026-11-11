import asyncio
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
    peak_settings,
    plan_submissions,
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
        SCENARIO_PEAK_PARTICIPANTS="99",
    )
    assert code == 2
    assert "SCENARIO_PEAK_PARTICIPANTS" in lines[0]


def test_cli_rejects_unknown_scenarios():
    with pytest.raises(SystemExit) as raised:
        main(["nao-existe"])
    assert raised.value.code == 2
