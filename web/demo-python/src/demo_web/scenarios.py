import asyncio
import logging
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from time import monotonic, perf_counter
from typing import Callable, Mapping
from uuid import UUID, uuid4

from demo_web.errors import InvalidResponseError, ServiceError, TransportError
from demo_web.models import (
    Activity,
    ActivityInput,
    Availability,
    Event,
    EventInput,
    Money,
    Registration,
)


MAX_SCENARIO_PARTICIPANTS = 20
MAX_OBSERVATIONS = 128
MAX_READBACK_REQUESTS = MAX_SCENARIO_PARTICIPANTS + 1
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScenarioPreset:
    key: str
    title: str
    participants: int
    capacity: int
    submissions_per_participant: int
    required_concurrency: int
    price_minor: int = 0


PRESETS = {
    "last-seat": ScenarioPreset(
        "last-seat",
        "Última vaga",
        participants=2,
        capacity=1,
        submissions_per_participant=1,
        required_concurrency=2,
    ),
    "registration-rush": ScenarioPreset(
        "registration-rush",
        "Pico de inscrições",
        participants=20,
        capacity=5,
        submissions_per_participant=1,
        required_concurrency=10,
    ),
    "duplicate-submission": ScenarioPreset(
        "duplicate-submission",
        "Envios repetidos",
        participants=1,
        capacity=1,
        submissions_per_participant=10,
        required_concurrency=10,
    ),
}


class ScenarioError(RuntimeError):
    """Base error for bounded Scenario Lab operations."""


class ScenarioDisabledError(ScenarioError):
    """Raised when the opt-in lab is unavailable."""


class PresetUnavailableError(ScenarioError):
    """Raised when an operator limit cannot safely run a fixed preset."""


class ScenarioConflictError(ScenarioError):
    """Raised when a global run is already active or the intent conflicts."""


class ScenarioAccessError(ScenarioError):
    """Raised when a session does not own a requested run."""


class ScenarioStateError(ScenarioError):
    """Raised when a run cannot perform the requested control action."""


@dataclass
class _Observation:
    participant: str
    idempotency_key: str | None = None
    state: str = "not_sent"
    status: str | None = None
    registration_id: UUID | None = None
    correlation_id: UUID | None = None
    error_code: str | None = None
    latency_ms: int | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


@dataclass
class _ScenarioRun:
    id: UUID
    owner_session_id: str
    preset: ScenarioPreset
    created_at: datetime
    created_monotonic: float
    activity: Activity | None = None
    event: Event | None = None
    state: str = "PREPARING"
    verdict: str = "INCONCLUSIVE"
    observations: list[_Observation] = field(default_factory=list)
    timeline: list[dict[str, object]] = field(default_factory=list)
    registrations: list[Registration] = field(default_factory=list)
    availability: Availability | None = None
    readback_at: datetime | None = None
    sample_requests: int = 0
    sample_errors: int = 0
    dispatch_count: int = 0
    completed_count: int = 0
    peak_in_flight: int = 0
    active_requests: int = 0
    errors: list[str] = field(default_factory=list)
    unknown_count: int = 0
    reconciliation_conflict: bool = False
    stop_event: asyncio.Event = field(default_factory=asyncio.Event)
    worker: asyncio.Task[None] | None = None
    continuation_key: str | None = None
    expected_fifo_registration_id: UUID | None = None
    promoted_registration_id: UUID | None = None
    accepted_registration_id: UUID | None = None
    cancel_registration_id: UUID | None = None
    cancel_subject_id: UUID | None = None
    accept_registration_id: UUID | None = None
    accept_subject_id: UUID | None = None
    continuation_verdict: str = "NOT_RUN"
    accept_key: str | None = None
    continuation_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


def select_fifo_candidate(
    registrations: list[Registration] | tuple[Registration, ...],
) -> Registration | None:
    candidates = [
        registration
        for registration in registrations
        if registration.status == "WAITLISTED" and registration.waitlist_sequence is not None
    ]
    return min(
        candidates,
        key=lambda registration: (
            registration.waitlist_sequence,
            registration.created_at,
            registration.id,
        ),
        default=None,
    )


def evaluate_verdict(
    *,
    preset: str,
    capacity: int,
    participant_count: int,
    dispatch_count: int,
    errors: list[str],
    unknown_count: int,
    final_statuses: list[str],
    occupied: int | None,
    readback_complete: bool,
    observed_capacity: int | None = None,
    observed_available: int | None = None,
    contradictory: bool = False,
) -> str:
    occupied_statuses = {"CONFIRMED", "OFFERED", "PENDING_PAYMENT"}
    if (
        contradictory
        or (occupied is not None and (occupied > capacity or occupied < 0))
        or (observed_capacity is not None and observed_capacity != capacity)
        or (
            occupied is not None
            and observed_available is not None
            and observed_available != max(0, capacity - occupied)
        )
    ):
        return "MISMATCH"
    if not readback_complete or errors or unknown_count:
        return "INCONCLUSIVE"

    expected_dispatches = participant_count
    if preset == "duplicate-submission":
        expected_dispatches = 10
    if dispatch_count != expected_dispatches:
        return "INCONCLUSIVE"

    occupied_count = sum(status in occupied_statuses for status in final_statuses)
    waiting_count = final_statuses.count("WAITLISTED")
    expected_occupied = min(capacity, participant_count)
    expected_waiting = max(0, participant_count - capacity)
    if preset == "duplicate-submission":
        expected_waiting = 0
    if (
        occupied != expected_occupied
        or occupied_count != expected_occupied
        or waiting_count != expected_waiting
    ):
        return "MISMATCH"
    return "EXPECTED"


def _setting(settings, name: str) -> int:
    value = getattr(settings, name)
    if not isinstance(value, int):
        raise ScenarioDisabledError("Scenario Lab limits are not configured.")
    return value


def _error_code(error: Exception) -> str:
    code = getattr(error, "code", "REQUEST_FAILED")
    return code if isinstance(code, str) and code.isascii() else "REQUEST_FAILED"


class ScenarioManager:
    def __init__(
        self,
        *,
        clients,
        settings,
        identities: Mapping[str, Mapping[str, str]],
        organizer_token: str,
        organization_id: str,
        session_is_active: Callable[[str], bool],
        clock: Callable[[], float] = monotonic,
    ):
        if not settings.lab_enabled:
            raise ScenarioDisabledError("Scenario Lab is disabled.")
        if not 1 <= len(identities) <= MAX_SCENARIO_PARTICIPANTS:
            raise ScenarioDisabledError("Scenario identity pool is outside its limit.")
        self._clients = clients
        self._settings = settings
        self._identities = tuple(
            (name, dict(identity)) for name, identity in sorted(identities.items())
        )
        self._organizer_token = organizer_token
        self._organization_id = UUID(organization_id)
        self._session_is_active = session_is_active
        self._clock = clock
        self._runs: OrderedDict[str, _ScenarioRun] = OrderedDict()
        self._active_run_id: str | None = None
        self._lock = asyncio.Lock()
        self._tasks: set[asyncio.Task[None]] = set()
        self._closed = False

    @property
    def active_run_id(self) -> str | None:
        return self._active_run_id

    @property
    def pending_task_count(self) -> int:
        return sum(not task.done() for task in self._tasks)

    def is_running_for(self, owner_session_id: str) -> bool:
        run = self._runs.get(self._active_run_id or "")
        return run is not None and run.owner_session_id == owner_session_id

    def preview(self, preset_key: str) -> dict[str, object]:
        preset = self._preset(preset_key)
        unavailable = self._unavailable_reason(preset)
        return {
            "preset": preset.key,
            "title": preset.title,
            "participants": preset.participants,
            "capacity": preset.capacity,
            "submissionsPerParticipant": preset.submissions_per_participant,
            "concurrency": preset.required_concurrency,
            "priceMinor": preset.price_minor,
            "available": unavailable is None,
            "unavailableReason": unavailable,
        }

    async def start(self, owner_session_id: str, preset_key: str) -> dict[str, object]:
        if self._closed:
            raise ScenarioDisabledError("Scenario Lab is shutting down.")
        if not self._session_is_active(owner_session_id):
            raise ScenarioAccessError("The browser session has expired.")
        preset = self._preset(preset_key)
        unavailable = self._unavailable_reason(preset)
        if unavailable is not None:
            raise PresetUnavailableError(unavailable)

        async with self._lock:
            self._prune()
            if self._active_run_id is not None:
                raise ScenarioConflictError("Another Scenario Lab run is active.")
            if self._closed:
                raise ScenarioDisabledError("Scenario Lab is shutting down.")
            run = _ScenarioRun(
                id=uuid4(),
                owner_session_id=owner_session_id,
                preset=preset,
                created_at=datetime.now(timezone.utc),
                created_monotonic=self._clock(),
            )
            self._runs[str(run.id)] = run
            self._active_run_id = str(run.id)

        try:
            await self._prepare(run)
        except (ServiceError, TransportError, InvalidResponseError) as error:
            run.state = "PARTIAL"
            run.verdict = "INCONCLUSIVE"
            run.errors.append(_error_code(error))
            self._release_active(run)
            self._prune()
            return self._snapshot(run)
        except Exception:
            run.state = "BLOCKED"
            run.verdict = "INCONCLUSIVE"
            run.errors.append("PREPARATION_FAILED")
            self._release_active(run)
            self._prune()
            raise

        run.state = "RUNNING"
        run.worker = asyncio.create_task(self._supervise(run))
        self._tasks.add(run.worker)
        run.worker.add_done_callback(self._tasks.discard)
        return self._snapshot(run)

    async def _supervise(self, run: _ScenarioRun) -> None:
        try:
            await self._execute(run)
        except asyncio.CancelledError:
            run.state = "STOPPED"
            run.verdict = "INCONCLUSIVE"
            raise
        except Exception as error:
            LOGGER.exception(
                "Scenario Lab worker failed.",
                extra={
                    "run_id": str(run.id),
                    "error_type": type(error).__name__,
                },
            )
            run.state = "PARTIAL"
            run.verdict = "INCONCLUSIVE"
            run.errors.append("RUNNER_FAILED")
        finally:
            self._release_active(run)
            self._prune()

    async def wait_for_run(self, owner_session_id: str, run_id: str) -> dict[str, object]:
        run = self._owned_run(owner_session_id, run_id)
        task = run.worker
        if task is not None:
            await asyncio.shield(task)
        return self._snapshot(run)

    def snapshot(self, owner_session_id: str, run_id: str) -> dict[str, object] | None:
        self._prune()
        run = self._runs.get(run_id)
        if run is None or run.owner_session_id != owner_session_id:
            return None
        return self._snapshot(run)

    def snapshots_for(self, owner_session_id: str) -> list[dict[str, object]]:
        self._prune()
        return [
            self._snapshot(run)
            for run in self._runs.values()
            if run.owner_session_id == owner_session_id
        ]

    def export(self, owner_session_id: str, run_id: str) -> dict[str, object] | None:
        snapshot = self.snapshot(owner_session_id, run_id)
        if snapshot is None:
            return None
        return {
            "schemaVersion": 1,
            "exportedAt": datetime.now(timezone.utc).isoformat(),
            "run": snapshot,
        }

    async def stop(self, owner_session_id: str, run_id: str) -> dict[str, object]:
        run = self._owned_run(owner_session_id, run_id)
        if run.worker is None or run.worker.done():
            return self._snapshot(run)
        run.stop_event.set()
        timeout = _setting(self._settings, "lab_settle_timeout_seconds")
        try:
            await asyncio.wait_for(asyncio.shield(run.worker), timeout=timeout)
        except TimeoutError:
            run.worker.cancel()
            await asyncio.gather(run.worker, return_exceptions=True)
            run.state = "STOPPED"
            run.verdict = "INCONCLUSIVE"
            self._release_active(run)
        return self._snapshot(run)

    async def cancel_confirmed(self, owner_session_id: str, run_id: str) -> dict[str, object]:
        run = self._owned_run(owner_session_id, run_id)
        async with run.continuation_lock:
            return await self._cancel_confirmed(run)

    async def _cancel_confirmed(self, run: _ScenarioRun) -> dict[str, object]:
        if run.state != "COMPLETED" or run.activity is None:
            raise ScenarioStateError("Only a completed run can continue.")
        if run.continuation_verdict in {"EXPECTED", "MISMATCH"}:
            return self._snapshot(run)
        if run.continuation_key is None:
            waiting = select_fifo_candidate(run.registrations)
            run.expected_fifo_registration_id = waiting.id if waiting else None
            confirmed = sorted(
                (
                    registration
                    for registration in run.registrations
                    if registration.activity_id == run.activity.id
                    and registration.status == "CONFIRMED"
                ),
                key=lambda registration: (registration.created_at, registration.id),
            )
            if not confirmed:
                raise ScenarioStateError("No run-owned confirmed registration is available.")
            registration = confirmed[0]
            run.cancel_registration_id = registration.id
            run.cancel_subject_id = registration.participant_id
            run.continuation_key = f"lab-{run.id}-cancel"
        else:
            if (
                run.cancel_registration_id is None
                or run.cancel_subject_id is None
                or run.accept_key is not None
            ):
                raise ScenarioStateError("This run has already continued.")
        if (
            run.cancel_registration_id is None
            or run.cancel_subject_id is None
            or run.continuation_key is None
        ):
            raise ScenarioStateError("This run has no pending cancellation.")
        token = self._token_for_subject(run.cancel_subject_id)
        try:
            cancelled = await self._clients.cancel_registration(
                run.cancel_registration_id,
                token=token,
                idempotency_key=run.continuation_key,
                session_id=f"lab:{run.id}",
                correlation_id=uuid4(),
            )
        except (ServiceError, TransportError, InvalidResponseError) as error:
            run.errors.append(_error_code(error))
            run.continuation_verdict = "INCONCLUSIVE"
            return self._snapshot(run)
        await self._reconcile(run, compare_dispatch_results=False)
        offered = [
            item
            for item in run.registrations
            if item.activity_id == run.activity.id and item.status == "OFFERED"
        ]
        run.promoted_registration_id = (
            min(offered, key=lambda item: (item.waitlist_sequence or 0, item.id)).id
            if offered
            else None
        )
        if run.sample_errors:
            run.continuation_verdict = "INCONCLUSIVE"
        else:
            run.continuation_verdict = (
                "EXPECTED"
                if cancelled.status == "CANCELLED"
                and run.promoted_registration_id == run.expected_fifo_registration_id
                else "MISMATCH"
            )
        return self._snapshot(run)

    async def accept_offer(self, owner_session_id: str, run_id: str) -> dict[str, object]:
        run = self._owned_run(owner_session_id, run_id)
        async with run.continuation_lock:
            return await self._accept_offer(run)

    async def _accept_offer(self, run: _ScenarioRun) -> dict[str, object]:
        if run.accept_key is not None and run.continuation_verdict in {"EXPECTED", "MISMATCH"}:
            return self._snapshot(run)
        if run.promoted_registration_id is None:
            raise ScenarioStateError("No run-owned offer was observed.")
        if run.accept_key is None:
            registration = next(
                (
                    item
                    for item in run.registrations
                    if item.id == run.promoted_registration_id and item.status == "OFFERED"
                ),
                None,
            )
            if registration is None:
                raise ScenarioStateError("The observed offer is no longer available.")
            run.accept_registration_id = registration.id
            run.accept_subject_id = registration.participant_id
            run.accept_key = f"lab-{run.id}-accept"
        if run.accept_registration_id is None or run.accept_subject_id is None:
            raise ScenarioStateError("This run has no pending acceptance.")
        token = self._token_for_subject(run.accept_subject_id)
        try:
            accepted = await self._clients.accept_offer(
                run.accept_registration_id,
                token=token,
                idempotency_key=run.accept_key,
                session_id=f"lab:{run.id}",
                correlation_id=uuid4(),
            )
        except (ServiceError, TransportError, InvalidResponseError) as error:
            run.errors.append(_error_code(error))
            run.continuation_verdict = "INCONCLUSIVE"
            return self._snapshot(run)
        run.accepted_registration_id = accepted.id
        await self._reconcile(run, compare_dispatch_results=False)
        readback = next(
            (item for item in run.registrations if item.id == accepted.id),
            None,
        )
        if run.sample_errors:
            run.continuation_verdict = "INCONCLUSIVE"
        else:
            run.continuation_verdict = (
                "EXPECTED"
                if accepted.status == "CONFIRMED"
                and readback is not None
                and readback.status == "CONFIRMED"
                else "MISMATCH"
            )
        return self._snapshot(run)

    async def shutdown(self) -> None:
        self._closed = True
        active = self._runs.get(self._active_run_id or "")
        if active is not None:
            active.stop_event.set()
            if active.worker is not None and not active.worker.done():
                timeout = _setting(self._settings, "lab_settle_timeout_seconds")
                try:
                    await asyncio.wait_for(
                        asyncio.shield(active.worker),
                        timeout=timeout,
                    )
                except TimeoutError:
                    active.worker.cancel()
                    await asyncio.gather(active.worker, return_exceptions=True)
                    active.state = "STOPPED"
                    active.verdict = "INCONCLUSIVE"
                finally:
                    self._release_active(active)
        remaining = tuple(self._tasks)
        if remaining:
            for task in remaining:
                task.cancel()
            await asyncio.gather(*remaining, return_exceptions=True)

    async def _execute(self, run: _ScenarioRun) -> None:
        if run.activity is None:
            run.state = "BLOCKED"
            self._release_active(run)
            return
        participants = self._identities[: run.preset.participants]
        run.observations = []
        requests: list[tuple[tuple[str, Mapping[str, str]], _Observation, str, UUID]] = []
        duplicate_key = f"lab-{run.id}-duplicate"
        duplicate_correlation = uuid4()
        for index, identity in enumerate(participants, start=1):
            participant_label = f"Participante simulado {index:02}"
            for _ in range(run.preset.submissions_per_participant):
                observation = _Observation(participant=participant_label)
                run.observations.append(observation)
                if run.preset.key == "duplicate-submission":
                    key = duplicate_key
                    correlation = duplicate_correlation
                else:
                    key = f"lab-{run.id}-participant-{index:02}"
                    correlation = uuid4()
                observation.idempotency_key = key
                requests.append((identity, observation, key, correlation))

        semaphore = asyncio.Semaphore(_setting(self._settings, "lab_max_concurrency"))
        launch = asyncio.Event()

        async def send(
            identity: tuple[str, Mapping[str, str]],
            observation: _Observation,
            key: str,
            correlation: UUID,
        ) -> None:
            await launch.wait()
            async with semaphore:
                if run.stop_event.is_set() or not self._session_is_active(run.owner_session_id):
                    run.stop_event.set()
                    return
                _, fixture = identity
                observation.correlation_id = correlation
                observation.state = "in_flight"
                observation.started_at = datetime.now(timezone.utc)
                run.dispatch_count += 1
                run.active_requests += 1
                run.peak_in_flight = max(run.peak_in_flight, run.active_requests)
                started = perf_counter()
                try:
                    registration = await self._clients.request_registration(
                        run.activity.id,
                        token=fixture["token"],
                        idempotency_key=key,
                        session_id=f"lab:{run.id}",
                        correlation_id=correlation,
                    )
                    observation.state = "success"
                    observation.status = registration.status
                    observation.registration_id = registration.id
                except asyncio.CancelledError:
                    observation.state = "unknown"
                    observation.error_code = "RUN_STOPPED"
                    run.unknown_count += 1
                    raise
                except (ServiceError, TransportError, InvalidResponseError) as error:
                    code = _error_code(error)
                    observation.error_code = code
                    if getattr(error, "outcome_unknown", False):
                        observation.state = "unknown"
                        run.unknown_count += 1
                    else:
                        observation.state = "error"
                        run.errors.append(code)
                        if code == "WAITLIST_DISABLED":
                            run.state = "BLOCKED"
                            run.stop_event.set()
                finally:
                    observation.latency_ms = max(0, round((perf_counter() - started) * 1000))
                    observation.completed_at = datetime.now(timezone.utc)
                    run.active_requests -= 1
                    run.completed_count += 1
                    self._append_timeline(
                        run,
                        {
                            "participant": observation.participant,
                            "state": observation.state,
                            "status": observation.status,
                            "registrationId": (
                                str(observation.registration_id)
                                if observation.registration_id
                                else None
                            ),
                            "errorCode": observation.error_code,
                            "latencyMs": observation.latency_ms,
                            "at": observation.completed_at.isoformat(),
                        },
                    )

        tasks = [asyncio.create_task(send(*request)) for request in requests]
        launch.set()
        timeout = _setting(self._settings, "lab_run_timeout_seconds")
        try:
            results = await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=timeout,
            )
            for request, result in zip(requests, results, strict=True):
                if isinstance(result, Exception):
                    observation = request[1]
                    observation.state = "error"
                    observation.error_code = "REQUEST_FAILED"
                    run.errors.append("REQUEST_FAILED")
                    LOGGER.error(
                        "Unexpected Scenario Lab registration failure.",
                        extra={
                            "run_id": str(run.id),
                            "error_type": type(result).__name__,
                        },
                    )
        except TimeoutError:
            run.stop_event.set()
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            run.state = "TIMED_OUT"
            run.errors.append("RUN_TIMEOUT")
        except asyncio.CancelledError:
            run.stop_event.set()
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            run.state = "STOPPED"
            run.verdict = "INCONCLUSIVE"
            raise

        await self._reconcile(run)
        if run.stop_event.is_set() and run.state == "RUNNING":
            run.state = "STOPPED"
        elif run.state == "RUNNING":
            run.state = "COMPLETED" if not run.errors and not run.unknown_count else "PARTIAL"
        self._evaluate(run)

    async def _reconcile(self, run: _ScenarioRun, *, compare_dispatch_results: bool = True) -> None:
        if run.activity is None:
            return
        participant_identities = self._identities[: run.preset.participants]
        if len(participant_identities) + 1 > MAX_READBACK_REQUESTS:
            run.sample_errors += 1
            return

        async def observe_availability() -> None:
            run.sample_requests += 1
            try:
                run.availability = await self._clients.get_availability(
                    run.activity.id,
                    session_id=f"lab:{run.id}",
                    correlation_id=uuid4(),
                )
            except (ServiceError, TransportError, InvalidResponseError):
                run.sample_errors += 1

        async def observe_registrations(identity: tuple[str, Mapping[str, str]]) -> None:
            _, fixture = identity
            run.sample_requests += 1
            try:
                registrations = await self._clients.list_registrations(
                    token=fixture["token"],
                    limit=50,
                    offset=0,
                    session_id=f"lab:{run.id}",
                    correlation_id=uuid4(),
                )
                run.registrations.extend(
                    registration
                    for registration in registrations
                    if registration.activity_id == run.activity.id
                )
            except (ServiceError, TransportError, InvalidResponseError):
                run.sample_errors += 1

        run.registrations.clear()
        run.sample_errors = 0
        tasks = [asyncio.create_task(observe_availability())]
        tasks.extend(
            asyncio.create_task(observe_registrations(identity))
            for identity in participant_identities
        )
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                run.sample_errors += 1
                LOGGER.error(
                    "Unexpected Scenario Lab read-back failure.",
                    extra={
                        "run_id": str(run.id),
                        "error_type": type(result).__name__,
                    },
                )
        run.readback_at = datetime.now(timezone.utc)
        if compare_dispatch_results:
            observed_by_id = {registration.id: registration for registration in run.registrations}
            subjects_by_participant = {
                f"Participante simulado {index:02}": UUID(fixture["subjectId"])
                for index, (_, fixture) in enumerate(participant_identities, start=1)
            }
            for observation in run.observations:
                if observation.state == "unknown":
                    subject_id = subjects_by_participant[observation.participant]
                    matches = [
                        registration
                        for registration in run.registrations
                        if registration.participant_id == subject_id
                    ]
                    if len(matches) == 1:
                        registration = matches[0]
                        observation.state = "reconciled"
                        observation.registration_id = registration.id
                        observation.status = registration.status
                        run.unknown_count -= 1
                        self._append_timeline(
                            run,
                            {
                                "participant": observation.participant,
                                "state": "reconciled",
                                "status": registration.status,
                                "registrationId": str(registration.id),
                                "source": "readback",
                                "at": run.readback_at.isoformat(),
                            },
                        )
                    elif len(matches) > 1:
                        run.reconciliation_conflict = True
                if observation.state == "success" and observation.registration_id:
                    final = observed_by_id.get(observation.registration_id)
                    if final is None or final.status != observation.status:
                        run.reconciliation_conflict = True

    def _evaluate(self, run: _ScenarioRun) -> None:
        if run.activity is None:
            run.verdict = "INCONCLUSIVE"
            return
        final_statuses = [
            registration.status
            for registration in run.registrations
            if registration.activity_id == run.activity.id
        ]
        errors = [*run.errors]
        if run.sample_errors:
            errors.append("READBACK_FAILED")
        run.verdict = evaluate_verdict(
            preset=run.preset.key,
            capacity=run.preset.capacity,
            participant_count=run.preset.participants,
            dispatch_count=run.dispatch_count,
            errors=errors,
            unknown_count=run.unknown_count,
            final_statuses=final_statuses,
            occupied=(run.availability.occupied if run.availability is not None else None),
            readback_complete=(run.availability is not None and run.sample_errors == 0),
            observed_capacity=(run.availability.capacity if run.availability is not None else None),
            observed_available=(
                run.availability.available if run.availability is not None else None
            ),
            contradictory=run.reconciliation_conflict,
        )

    async def _prepare(self, run: _ScenarioRun) -> None:
        now = datetime.now(timezone.utc)
        starts_at = now + timedelta(minutes=15)
        ends_at = starts_at + timedelta(hours=1)
        event_input = EventInput(
            organizationId=self._organization_id,
            title=(
                f"[CENÁRIO][SIMULADO][GESAD/UECE] "
                f"{run.preset.title} · {str(run.id)[:8]}"
            ),
            description=(
                "Evento acadêmico fictício do GESAD/UECE para experimento controlado. "
                "Os registros são mantidos após a execução."
            ),
            startsAt=starts_at,
            endsAt=ends_at + timedelta(minutes=5),
        )
        event = await self._clients.create_event(
            event_input,
            token=self._organizer_token,
            idempotency_key=f"lab-{run.id}-event",
            session_id=f"lab:{run.id}",
            correlation_id=run.id,
        )
        activity_input = ActivityInput(
            title=f"[CENÁRIO][SIMULADO][GESAD/UECE] {run.preset.title} · {str(run.id)[:8]}",
            startsAt=starts_at,
            endsAt=ends_at,
            location="Laboratório de demonstração GESAD/UECE",
            responsible="Equipe simulada do GESAD/UECE",
            capacity=run.preset.capacity,
            price=Money(amountMinor=0, currency="BRL"),
        )
        activity = await self._clients.create_activity(
            event.id,
            activity_input,
            token=self._organizer_token,
            idempotency_key=f"lab-{run.id}-activity",
            session_id=f"lab:{run.id}",
            correlation_id=run.id,
        )
        published = await self._clients.publish_event(
            event.id,
            token=self._organizer_token,
            idempotency_key=f"lab-{run.id}-publish",
            session_id=f"lab:{run.id}",
            correlation_id=run.id,
        )
        run.event = published
        run.activity = activity

    def _preset(self, preset_key: str) -> ScenarioPreset:
        try:
            return PRESETS[preset_key]
        except KeyError:
            raise ScenarioStateError("The requested preset is not available.") from None

    def _unavailable_reason(self, preset: ScenarioPreset) -> str | None:
        participants = _setting(self._settings, "lab_max_participants")
        concurrency = _setting(self._settings, "lab_max_concurrency")
        if participants < preset.participants:
            return "The configured participant limit is below this fixed preset."
        if concurrency < preset.required_concurrency:
            return "The configured concurrency limit is below this fixed preset."
        if len(self._identities) < preset.participants:
            return "The identity fixture does not contain enough participants."
        return None

    def _owned_run(self, owner_session_id: str, run_id: str) -> _ScenarioRun:
        self._prune()
        run = self._runs.get(run_id)
        if run is None:
            raise ScenarioAccessError("The requested run is no longer available.")
        if run.owner_session_id != owner_session_id:
            raise ScenarioAccessError("The requested run is not owned by this session.")
        return run

    def _token_for_subject(self, subject_id: UUID) -> str:
        for _, fixture in self._identities:
            if UUID(fixture["subjectId"]) == subject_id:
                return fixture["token"]
        raise ScenarioStateError("The registration is outside this run's participants.")

    def _release_active(self, run: _ScenarioRun) -> None:
        if self._active_run_id == str(run.id):
            self._active_run_id = None

    def _prune(self) -> None:
        now = self._clock()
        ttl = _setting(self._settings, "lab_run_ttl_seconds")
        for run_id, run in tuple(self._runs.items()):
            if run_id != self._active_run_id and now - run.created_monotonic >= ttl:
                del self._runs[run_id]
        maximum = _setting(self._settings, "lab_max_retained_runs")
        while len(self._runs) > maximum:
            removable = next(
                (run_id for run_id, run in self._runs.items() if run_id != self._active_run_id),
                None,
            )
            if removable is None:
                break
            del self._runs[removable]

    @staticmethod
    def _append_timeline(run: _ScenarioRun, entry: dict[str, object]) -> None:
        run.timeline.append(entry)
        if len(run.timeline) > MAX_OBSERVATIONS:
            del run.timeline[: len(run.timeline) - MAX_OBSERVATIONS]

    def _snapshot(self, run: _ScenarioRun) -> dict[str, object]:
        return {
            "id": str(run.id),
            "preset": run.preset.key,
            "title": run.preset.title,
            "state": run.state,
            "verdict": run.verdict,
            "createdAt": run.created_at.isoformat(),
            "elapsedMs": max(0, round((self._clock() - run.created_monotonic) * 1000)),
            "eventId": str(run.event.id) if run.event else None,
            "activityId": str(run.activity.id) if run.activity else None,
            "capacity": run.preset.capacity,
            "participantCount": run.preset.participants,
            "workload": {
                "dispatchCount": run.dispatch_count,
                "completedCount": run.completed_count,
                "peakInFlight": run.peak_in_flight,
                "errors": len(run.errors),
                "unknown": run.unknown_count,
                "throughputPerSecond": (
                    round(
                        run.dispatch_count / max(self._clock() - run.created_monotonic, 0.001),
                        2,
                    )
                ),
            },
            "sampleRequests": run.sample_requests,
            "sampleBudget": min(MAX_READBACK_REQUESTS, run.preset.participants + 1)
            * (1 + int(run.continuation_key is not None) + int(run.accept_key is not None)),
            "readback": {
                "observedAt": run.readback_at.isoformat() if run.readback_at else None,
                "availability": (
                    {
                        "capacity": run.availability.capacity,
                        "occupied": run.availability.occupied,
                        "available": run.availability.available,
                    }
                    if run.availability
                    else None
                ),
                "registrationCount": len(run.registrations),
                "statuses": [registration.status for registration in run.registrations],
                "sampleErrors": run.sample_errors,
            },
            "observations": [
                {
                    "participant": observation.participant,
                    "state": observation.state,
                    "status": observation.status,
                    "registrationId": (
                        str(observation.registration_id) if observation.registration_id else None
                    ),
                    "correlationId": (
                        str(observation.correlation_id) if observation.correlation_id else None
                    ),
                    "errorCode": observation.error_code,
                    "latencyMs": observation.latency_ms,
                }
                for observation in run.observations
            ],
            "timeline": list(run.timeline),
            "errors": list(run.errors),
            "fifo": {
                "expectedRegistrationId": (
                    str(run.expected_fifo_registration_id)
                    if run.expected_fifo_registration_id
                    else None
                ),
                "offeredRegistrationId": (
                    str(run.promoted_registration_id) if run.promoted_registration_id else None
                ),
                "acceptedRegistrationId": (
                    str(run.accepted_registration_id) if run.accepted_registration_id else None
                ),
                "verdict": run.continuation_verdict,
            },
        }
