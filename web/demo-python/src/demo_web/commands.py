import asyncio
import hashlib
import json
from dataclasses import dataclass
from typing import Awaitable, Callable, Mapping
from uuid import UUID, uuid4

from demo_web.sessions import BrowserSession


class CommandConflictError(ValueError):
    """Raised when an existing command key is reused for another intent."""


@dataclass(frozen=True)
class CommandOutcome:
    result: object
    idempotency_key: str
    correlation_id: UUID


@dataclass
class _CommandRecord:
    payload_hash: str
    persona: str
    persona_version: int
    correlation_id: UUID
    state: str = "running"
    task: asyncio.Task[CommandOutcome] | None = None
    outcome: CommandOutcome | None = None
    error: Exception | None = None


CommandOperation = Callable[[str, UUID], Awaitable[object]]


class CommandLedger:
    def __init__(self, *, max_commands_per_session: int):
        self._max_commands_per_session = max_commands_per_session

    async def execute(
        self,
        session: BrowserSession,
        *,
        idempotency_key: str,
        payload: Mapping[str, object],
        operation: CommandOperation,
    ) -> CommandOutcome:
        if not idempotency_key or len(idempotency_key) > 200:
            raise ValueError("An idempotency key of 1 to 200 characters is required.")
        try:
            serialized_payload = json.dumps(
                payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
            )
        except (TypeError, ValueError):
            raise ValueError("Command payload must contain only JSON values.") from None
        payload_hash = hashlib.sha256(serialized_payload.encode("utf-8")).hexdigest()

        with session.lock:
            existing = session.commands.get(idempotency_key)
            if existing is not None:
                if not isinstance(existing, _CommandRecord):
                    raise RuntimeError("The session command ledger is corrupted.")
                record = existing
                if (
                    record.payload_hash != payload_hash
                    or record.persona != session.persona
                    or record.persona_version != session.persona_version
                ):
                    raise CommandConflictError(
                        "This command key belongs to a different or invalidated intent."
                    )
                if record.state == "completed":
                    if record.outcome is None:
                        raise RuntimeError("The completed command has no outcome.")
                    return record.outcome
                if record.state == "failed":
                    if record.error is None:
                        raise RuntimeError("The failed command has no recorded error.")
                    raise record.error
                if record.state == "running":
                    task = record.task
                    if task is None:
                        raise RuntimeError("The running command has no execution task.")
                else:
                    task = self._start(session, idempotency_key, record, operation)
            else:
                if len(session.commands) >= self._max_commands_per_session:
                    raise OverflowError("This browser session has reached its command limit.")
                record = _CommandRecord(
                    payload_hash=payload_hash,
                    persona=session.persona,
                    persona_version=session.persona_version,
                    correlation_id=uuid4(),
                )
                session.commands[idempotency_key] = record
                task = self._start(session, idempotency_key, record, operation)

        return await asyncio.shield(task)

    @staticmethod
    def _start(
        session: BrowserSession,
        idempotency_key: str,
        record: _CommandRecord,
        operation: CommandOperation,
    ) -> asyncio.Task[CommandOutcome]:
        record.state = "running"
        record.error = None
        record.task = asyncio.create_task(
            CommandLedger._run(session, idempotency_key, record, operation)
        )
        return record.task

    @staticmethod
    async def _run(
        session: BrowserSession,
        idempotency_key: str,
        record: _CommandRecord,
        operation: CommandOperation,
    ) -> CommandOutcome:
        try:
            result = await operation(idempotency_key, record.correlation_id)
        except Exception as error:
            with session.lock:
                record.state = (
                    "uncertain"
                    if getattr(error, "outcome_unknown", False) or isinstance(error, TimeoutError)
                    else "failed"
                )
                record.error = error
                record.task = None
            raise
        except asyncio.CancelledError:
            with session.lock:
                record.state = "uncertain"
                record.task = None
            raise

        outcome = CommandOutcome(
            result=result,
            idempotency_key=idempotency_key,
            correlation_id=record.correlation_id,
        )
        with session.lock:
            record.state = "completed"
            record.outcome = outcome
            record.task = None
        return outcome
