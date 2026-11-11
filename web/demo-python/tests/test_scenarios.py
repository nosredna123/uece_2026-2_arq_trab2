import asyncio
from functools import wraps
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import pytest

from demo_web.errors import ServiceError, TransportError
from demo_web.models import (
    Activity,
    Availability,
    Event,
    Registration,
)
from demo_web.scenarios import (
    PRESETS,
    PresetUnavailableError,
    ScenarioAccessError,
    ScenarioConflictError,
    ScenarioManager,
    evaluate_verdict,
    select_fifo_candidate,
)
from demo_web.settings import Settings


ROOT = Path(__file__).resolve().parents[3]
SCENARIO_FIXTURES = ROOT / "mocks/identity-access/scenario-participants.json"
ORGANIZATION_ID = UUID("30000000-0000-4000-8000-000000000001")


def run_async_test(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        asyncio.run(function(*args, **kwargs))

    return wrapper


def _settings(web_environment, **overrides):
    values = {
        **web_environment,
        "IDENTITY_LAB_ENABLED": "true",
        "DEMO_LAB_ENABLED": "true",
        "DEMO_LAB_FIXTURES_PATH": str(SCENARIO_FIXTURES),
        "DEMO_LAB_MAX_PARTICIPANTS": "50",
        "DEMO_LAB_MAX_CONCURRENCY": "10",
        "DEMO_LAB_RUN_TIMEOUT_SECONDS": "60",
        "DEMO_LAB_SETTLE_TIMEOUT_SECONDS": "1",
        "DEMO_LAB_RUN_TTL_SECONDS": "1800",
        "DEMO_LAB_MAX_RETAINED_RUNS": "5",
        **overrides,
    }
    return Settings.from_environment(values)


def _identities():
    return json.loads(SCENARIO_FIXTURES.read_text(encoding="utf-8"))


def _registration(
    registration_id: int,
    participant_id: UUID,
    activity_id: UUID,
    status: str,
    *,
    sequence: int | None = None,
) -> Registration:
    now = datetime.now(timezone.utc)
    return Registration(
        id=UUID(f"60000000-0000-4000-8000-{registration_id:012d}"),
        activityId=activity_id,
        participantId=participant_id,
        organizationId=ORGANIZATION_ID,
        status=status,
        refundStatus="NONE",
        createdAt=now + timedelta(microseconds=registration_id),
        version=1,
        waitlistSequence=sequence,
        price={"amountMinor": 0, "currency": "BRL"},
    )


class FakeClients:
    def __init__(self):
        self.events = []
        self.activities = []
        self.registration_calls = []
        self.registrations = {}
        self.tokens = {}
        self.next_event = 1
        self.next_activity = 1
        self.next_registration = 1
        self.occupied_override = None
        self.fail_tokens = set()
        self.unknown_tokens = set()
        self.commit_then_timeout_tokens = set()
        self.fail_cancel_once = False
        self.cancel_calls = []
        self.accept_calls = []
        self.hold_registration = None
        self.registration_started = asyncio.Event()
        self.active_requests = 0
        self.peak_requests = 0
        self._lock = asyncio.Lock()

    async def create_event(self, event, *, token, **_):
        self.events.append((event, token))
        value = Event(
            id=UUID(f"61000000-0000-4000-8000-{self.next_event:012d}"),
            **event.model_dump(by_alias=True),
            status="DRAFT",
            version=1,
        )
        self.next_event += 1
        return value

    async def create_activity(self, event_id, activity, *, token, **_):
        self.activities.append((activity, str(event_id), token))
        value = Activity(
            id=UUID(f"62000000-0000-4000-8000-{self.next_activity:012d}"),
            eventId=event_id,
            organizationId=ORGANIZATION_ID,
            **activity.model_dump(by_alias=True),
            version=1,
            capacityFrozen=False,
        )
        self.next_activity += 1
        return value

    async def publish_event(self, event_id, *, token, **_):
        event, _ = self.events[-1]
        return Event(
            id=event_id,
            **event.model_dump(by_alias=True),
            status="PUBLISHED",
            version=2,
        )

    async def request_registration(
        self, activity_id, *, token, idempotency_key, correlation_id, **kwargs
    ):
        self.registration_calls.append(
            {
                "activity_id": str(activity_id),
                "token": token,
                "idempotency_key": idempotency_key,
                "correlation_id": correlation_id,
            }
        )
        if token in self.fail_tokens:
            raise ServiceError(
                service="registration",
                method="POST",
                status_code=409,
                code="WAITLIST_DISABLED",
                message="The waitlist is disabled.",
                correlation_id=correlation_id,
            )
        if token in self.unknown_tokens:
            raise TransportError(
                service="registration",
                method="POST",
                code="HTTP_TIMEOUT",
                message="The request timed out.",
                correlation_id=correlation_id,
                outcome_unknown=True,
            )
        self.active_requests += 1
        self.peak_requests = max(self.peak_requests, self.active_requests)
        self.registration_started.set()
        try:
            if self.hold_registration is not None:
                await self.hold_registration.wait()
            else:
                await asyncio.sleep(0.002)
            identity = next(
                identity for identity in self.tokens.values() if identity["token"] == token
            )
            key = (token, idempotency_key, str(activity_id))
            async with self._lock:
                existing = self.registrations.get(key)
                if existing is not None:
                    return existing
                activity = self.activities[-1][0]
                occupied = sum(
                    item.activity_id == UUID(str(activity_id))
                    and item.status in {"CONFIRMED", "OFFERED"}
                    for item in self.registrations.values()
                )
                if occupied < activity.capacity:
                    status = "CONFIRMED"
                    sequence = None
                else:
                    status = "WAITLISTED"
                    sequence = (
                        sum(
                            item.activity_id == UUID(str(activity_id))
                            and item.status == "WAITLISTED"
                            for item in self.registrations.values()
                        )
                        + 1
                    )
                registration = _registration(
                    self.next_registration,
                    UUID(identity["subjectId"]),
                    UUID(str(activity_id)),
                    status,
                    sequence=sequence,
                )
                self.next_registration += 1
                self.registrations[key] = registration
                if token in self.commit_then_timeout_tokens:
                    self.commit_then_timeout_tokens.remove(token)
                    raise TransportError(
                        service="registration",
                        method="POST",
                        code="HTTP_TIMEOUT",
                        message="The response timed out after commit.",
                        correlation_id=correlation_id,
                        outcome_unknown=True,
                    )
                return registration
        finally:
            self.active_requests -= 1

    async def get_availability(self, activity_id, **_):
        activity = self.activities[-1][0]
        occupied = (
            self.occupied_override
            if self.occupied_override is not None
            else sum(
                item.activity_id == UUID(str(activity_id))
                and item.status in {"CONFIRMED", "OFFERED"}
                for item in self.registrations.values()
            )
        )
        return Availability(
            activityId=activity_id,
            capacity=activity.capacity,
            occupied=occupied,
            available=max(0, activity.capacity - occupied),
            asOf=datetime.now(timezone.utc),
        )

    async def list_registrations(self, *, token, **_):
        identity = next(identity for identity in self.tokens.values() if identity["token"] == token)
        subject_id = UUID(identity["subjectId"])
        return [item for item in self.registrations.values() if item.participant_id == subject_id]

    async def cancel_registration(self, registration_id, *, token, idempotency_key, **_):
        self.cancel_calls.append((str(registration_id), idempotency_key))
        if self.fail_cancel_once:
            self.fail_cancel_once = False
            raise TransportError(
                service="registration",
                method="POST",
                code="HTTP_TIMEOUT",
                message="The request timed out.",
                correlation_id=UUID(int=1),
                outcome_unknown=True,
            )
        current = next(
            item for item in self.registrations.values() if item.id == UUID(str(registration_id))
        )
        cancelled = current.model_copy(update={"status": "CANCELLED", "version": 2})
        for key, value in tuple(self.registrations.items()):
            if value.id == current.id:
                self.registrations[key] = cancelled
        waiting = sorted(
            (
                item
                for item in self.registrations.values()
                if item.activity_id == current.activity_id and item.status == "WAITLISTED"
            ),
            key=lambda item: (item.waitlist_sequence, item.id),
        )
        if waiting:
            offered = waiting[0].model_copy(update={"status": "OFFERED", "version": 2})
            for key, value in tuple(self.registrations.items()):
                if value.id == offered.id:
                    self.registrations[key] = offered
        return cancelled

    async def accept_offer(self, registration_id, *, token, idempotency_key, **_):
        self.accept_calls.append((str(registration_id), idempotency_key))
        current = next(
            item for item in self.registrations.values() if item.id == UUID(str(registration_id))
        )
        accepted = current.model_copy(update={"status": "CONFIRMED", "version": 3})
        for key, value in tuple(self.registrations.items()):
            if value.id == current.id:
                self.registrations[key] = accepted
        return accepted


def _manager(web_environment, clients, *, session_active=lambda _: True, clock=None):
    baseline = json.loads(
        Path(web_environment["DEMO_WEB_IDENTITY_FIXTURES_PATH"]).read_text(encoding="utf-8")
    )
    arguments = {
        "clients": clients,
        "settings": _settings(web_environment),
        "identities": _identities(),
        "organizer_token": baseline["organizer"]["token"],
        "organization_id": baseline["organizer"]["organizationId"],
        "session_is_active": session_active,
    }
    if clock is not None:
        arguments["clock"] = clock
    clients.tokens = _identities()
    return ScenarioManager(**arguments)


async def _start_and_finish(manager, preset, owner="session-one"):
    started = await manager.start(owner, preset)
    return await manager.wait_for_run(owner, started["id"])


def test_presets_are_fixed_and_use_only_free_bounded_workloads():
    assert set(PRESETS) == {"last-seat", "registration-rush", "duplicate-submission"}
    assert PRESETS["last-seat"].participants == 2
    assert PRESETS["last-seat"].capacity == 1
    assert PRESETS["registration-rush"].participants == 20
    assert PRESETS["registration-rush"].capacity == 5
    assert PRESETS["duplicate-submission"].submissions_per_participant == 10
    assert all(preset.price_minor == 0 for preset in PRESETS.values())


@run_async_test
async def test_last_seat_uses_a_start_barrier_and_reconciles_real_results(web_environment):
    clients = FakeClients()
    manager = _manager(web_environment, clients)

    result = await _start_and_finish(manager, "last-seat")

    event, _ = clients.events[0]
    activity, _, _ = clients.activities[0]
    assert event.organization_id == ORGANIZATION_ID
    assert event.title.startswith("[CENÁRIO][SIMULADO][GESAD/UECE]")
    assert activity.title.startswith("[CENÁRIO][SIMULADO][GESAD/UECE]")
    assert "Participante simulado 01" in {
        observation["participant"] for observation in result["observations"]
    }
    assert len(clients.registration_calls) == 2
    assert clients.peak_requests == 2
    assert result["workload"]["dispatchCount"] == 2
    assert result["readback"]["availability"]["occupied"] == 1
    assert sorted(item["status"] for item in result["observations"]) == [
        "CONFIRMED",
        "WAITLISTED",
    ]
    assert result["verdict"] == "EXPECTED"
    await manager.shutdown()


@run_async_test
async def test_registration_rush_is_concurrent_and_observer_budget_is_bounded(
    web_environment,
):
    clients = FakeClients()
    manager = _manager(web_environment, clients)

    result = await _start_and_finish(manager, "registration-rush")

    assert len(clients.registration_calls) == 20
    assert clients.peak_requests == 10
    assert result["workload"]["dispatchCount"] == 20
    assert result["workload"]["peakInFlight"] == 10
    assert result["sampleRequests"] == 21
    assert result["sampleBudget"] == 21
    assert result["readback"]["availability"]["occupied"] == 5
    assert result["verdict"] == "EXPECTED"
    await manager.shutdown()


@run_async_test
async def test_duplicate_preset_sends_ten_parallel_replays_with_one_key(
    web_environment,
):
    clients = FakeClients()
    manager = _manager(web_environment, clients)

    result = await _start_and_finish(manager, "duplicate-submission")

    assert len(clients.registration_calls) == 10
    assert len({call["idempotency_key"] for call in clients.registration_calls}) == 1
    assert len({call["correlation_id"] for call in clients.registration_calls}) == 1
    registration_ids = {item["registrationId"] for item in result["observations"]}
    assert len(registration_ids) == 1
    assert result["readback"]["registrationCount"] == 1
    assert result["verdict"] == "EXPECTED"
    await manager.shutdown()


@run_async_test
async def test_each_run_prepares_a_new_future_free_activity(web_environment):
    clients = FakeClients()
    manager = _manager(web_environment, clients)

    first = await _start_and_finish(manager, "last-seat")
    second = await _start_and_finish(manager, "last-seat")
    first_activity = first["activityId"]
    second_activity = second["activityId"]

    assert first_activity != second_activity
    assert len(clients.activities) == 2
    assert all(activity.price.amount_minor == 0 for activity, _, _ in clients.activities)
    assert all(
        activity.starts_at > datetime.now(timezone.utc) for activity, _, _ in clients.activities
    )
    assert clients.activities[0][0].title != clients.activities[1][0].title
    await manager.shutdown()


@run_async_test
async def test_one_global_run_and_owner_only_snapshots(web_environment):
    clients = FakeClients()
    clients.hold_registration = asyncio.Event()
    manager = _manager(web_environment, clients)
    started = await manager.start("owner-one", "registration-rush")
    await clients.registration_started.wait()

    with pytest.raises(ScenarioConflictError):
        await manager.start("owner-two", "last-seat")
    assert manager.snapshot("owner-two", started["id"]) is None
    with pytest.raises(ScenarioAccessError):
        await manager.stop("owner-two", started["id"])

    clients.hold_registration.set()
    await manager.wait_for_run("owner-one", started["id"])
    await manager.shutdown()


@run_async_test
async def test_partial_service_failures_are_not_success_shaped(web_environment):
    clients = FakeClients()
    clients.fail_tokens.add(_identities()["scenarioParticipant01"]["token"])
    manager = _manager(web_environment, clients)

    result = await _start_and_finish(manager, "registration-rush")

    failed = next(
        item
        for item in result["observations"]
        if item["participant"] == "Participante simulado 01"
    )
    assert failed["state"] == "error"
    assert failed["errorCode"] == "WAITLIST_DISABLED"
    assert result["verdict"] == "INCONCLUSIVE"
    await manager.shutdown()


@run_async_test
async def test_disabled_waitlist_blocks_remaining_dispatches(web_environment):
    clients = FakeClients()
    clients.fail_tokens.update(identity["token"] for identity in _identities().values())
    manager = _manager(web_environment, clients)

    result = await _start_and_finish(manager, "registration-rush")

    assert result["state"] == "BLOCKED"
    assert "WAITLIST_DISABLED" in result["errors"]
    assert result["workload"]["dispatchCount"] <= 10
    assert result["verdict"] == "INCONCLUSIVE"
    await manager.shutdown()


@run_async_test
async def test_unknown_mutation_outcome_stays_unknown(web_environment):
    clients = FakeClients()
    clients.unknown_tokens.add(_identities()["scenarioParticipant01"]["token"])
    manager = _manager(web_environment, clients)

    result = await _start_and_finish(manager, "registration-rush")

    unknown = next(
        item
        for item in result["observations"]
        if item["participant"] == "Participante simulado 01"
    )
    assert unknown["state"] == "unknown"
    assert unknown["errorCode"] == "HTTP_TIMEOUT"
    assert result["verdict"] == "INCONCLUSIVE"
    await manager.shutdown()


@run_async_test
async def test_public_readback_resolves_a_committed_unknown_outcome(web_environment):
    clients = FakeClients()
    clients.commit_then_timeout_tokens.add(_identities()["scenarioParticipant01"]["token"])
    manager = _manager(web_environment, clients)

    result = await _start_and_finish(manager, "registration-rush")

    resolved = next(
        item
        for item in result["observations"]
        if item["participant"] == "Participante simulado 01"
    )
    assert resolved["state"] == "reconciled"
    assert resolved["status"] in {"CONFIRMED", "WAITLISTED"}
    assert result["workload"]["unknown"] == 0
    assert result["verdict"] == "EXPECTED"
    await manager.shutdown()


@run_async_test
async def test_stop_drains_in_flight_and_prevents_queued_dispatch(web_environment):
    clients = FakeClients()
    clients.hold_registration = asyncio.Event()
    manager = _manager(
        web_environment,
        clients,
    )
    started = await manager.start("session-one", "registration-rush")
    await clients.registration_started.wait()

    stopped = await manager.stop("session-one", started["id"])

    assert stopped["state"] == "STOPPED"
    assert stopped["workload"]["dispatchCount"] <= 10
    assert all(item["state"] != "success" for item in stopped["observations"])
    assert manager.active_run_id is None
    await manager.shutdown()


@run_async_test
async def test_session_expiry_stops_new_work_after_in_flight_drain(web_environment):
    clients = FakeClients()
    clients.hold_registration = asyncio.Event()
    active_sessions = {"session-one": True}
    manager = _manager(
        web_environment,
        clients,
        session_active=lambda session_id: active_sessions.get(session_id, False),
    )
    started = await manager.start("session-one", "registration-rush")
    await clients.registration_started.wait()
    active_sessions["session-one"] = False
    clients.hold_registration.set()

    result = await manager.wait_for_run("session-one", started["id"])

    assert result["workload"]["dispatchCount"] <= 10
    assert result["state"] in {"STOPPED", "INCONCLUSIVE"}
    await manager.shutdown()


@run_async_test
async def test_shutdown_awaits_worker_and_leaves_no_background_task(web_environment):
    clients = FakeClients()
    clients.hold_registration = asyncio.Event()
    manager = _manager(web_environment, clients)
    started = await manager.start("session-one", "registration-rush")
    await clients.registration_started.wait()

    await manager.shutdown()

    assert manager.pending_task_count == 0
    assert manager.active_run_id is None
    result = manager.snapshot("session-one", started["id"])
    assert result["state"] != "RUNNING"


def test_fifo_candidate_is_selected_by_backend_sequence_not_actor_order():
    activity_id = UUID("62000000-0000-4000-8000-000000000001")
    later = _registration(1, UUID(int=1), activity_id, "WAITLISTED", sequence=2)
    earlier = _registration(2, UUID(int=2), activity_id, "WAITLISTED", sequence=1)

    assert select_fifo_candidate([later, earlier]) == earlier


def test_contradictory_availability_is_a_mismatch_not_a_clamped_pass():
    verdict = evaluate_verdict(
        preset="last-seat",
        capacity=1,
        participant_count=2,
        dispatch_count=2,
        errors=[],
        unknown_count=0,
        final_statuses=["CONFIRMED", "WAITLISTED"],
        occupied=2,
        readback_complete=True,
    )

    assert verdict == "MISMATCH"


def test_inconsistent_availability_capacity_and_free_count_are_mismatches():
    base = {
        "preset": "last-seat",
        "capacity": 1,
        "participant_count": 2,
        "dispatch_count": 2,
        "errors": [],
        "unknown_count": 0,
        "final_statuses": ["CONFIRMED", "WAITLISTED"],
        "occupied": 1,
        "readback_complete": True,
    }

    assert evaluate_verdict(**base, observed_capacity=2) == "MISMATCH"
    assert evaluate_verdict(**base, observed_available=0) == "EXPECTED"
    assert evaluate_verdict(**base, observed_available=1) == "MISMATCH"


def test_reconciled_expected_counts_produce_a_sampled_verdict():
    verdict = evaluate_verdict(
        preset="registration-rush",
        capacity=5,
        participant_count=20,
        dispatch_count=20,
        errors=[],
        unknown_count=0,
        final_statuses=["CONFIRMED"] * 5 + ["WAITLISTED"] * 15,
        occupied=5,
        readback_complete=True,
    )

    assert verdict == "EXPECTED"
    assert "PROVED" not in verdict


@run_async_test
async def test_cancel_then_accept_observes_backend_fifo_and_reconciles_each_step(
    web_environment,
):
    clients = FakeClients()
    clients.fail_cancel_once = True
    manager = _manager(web_environment, clients)
    completed = await _start_and_finish(manager, "last-seat")

    uncertain = await manager.cancel_confirmed("session-one", completed["id"])
    assert uncertain["fifo"]["verdict"] == "INCONCLUSIVE"
    assert uncertain["sampleRequests"] == 3

    cancelled = await manager.cancel_confirmed("session-one", completed["id"])
    expected_offer = cancelled["fifo"]["expectedRegistrationId"]
    assert cancelled["fifo"]["offeredRegistrationId"] == expected_offer
    assert cancelled["fifo"]["verdict"] == "EXPECTED"
    assert cancelled["sampleRequests"] == 6
    assert cancelled["sampleBudget"] == 6
    await manager.cancel_confirmed("session-one", completed["id"])
    assert len(clients.cancel_calls) == 2
    assert clients.cancel_calls[0] == clients.cancel_calls[1]

    accepted = await manager.accept_offer("session-one", completed["id"])
    assert accepted["fifo"]["acceptedRegistrationId"] == expected_offer
    assert accepted["fifo"]["verdict"] == "EXPECTED"
    assert accepted["sampleRequests"] == 9
    assert accepted["sampleBudget"] == 9
    await manager.accept_offer("session-one", completed["id"])
    assert len(clients.accept_calls) == 1
    await manager.shutdown()


@run_async_test
async def test_fresh_run_limit_and_expired_observations_are_bounded(web_environment):
    clients = FakeClients()
    clock = [0.0]
    manager = _manager(
        web_environment,
        clients,
        clock=lambda: clock[0],
    )
    manager._settings = _settings(
        web_environment,
        DEMO_LAB_RUN_TIMEOUT_SECONDS="1",
        DEMO_LAB_SETTLE_TIMEOUT_SECONDS="1",
        DEMO_LAB_RUN_TTL_SECONDS="3",
        DEMO_LAB_MAX_RETAINED_RUNS="1",
    )

    first = await _start_and_finish(manager, "last-seat")
    second = await _start_and_finish(manager, "last-seat")

    assert manager.snapshot("session-one", first["id"]) is None
    assert manager.snapshot("session-one", second["id"]) is not None
    clock[0] = 4.0
    assert manager.snapshot("session-one", second["id"]) is None
    await manager.shutdown()


@run_async_test
async def test_lower_operator_limits_disable_presets_instead_of_resizing(web_environment):
    clients = FakeClients()
    manager = _manager(
        web_environment,
        clients,
    )
    manager._settings = _settings(
        web_environment,
        DEMO_LAB_MAX_PARTICIPANTS="10",
        DEMO_LAB_MAX_CONCURRENCY="2",
    )

    with pytest.raises(PresetUnavailableError):
        await manager.start("session-one", "registration-rush")
    with pytest.raises(PresetUnavailableError):
        await manager.start("session-one", "duplicate-submission")
    await manager.shutdown()
