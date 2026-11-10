import asyncio

import pytest

from demo_web.commands import (
    CommandConflictError,
    CommandLedger,
)
from demo_web.errors import TransportError
from demo_web.sessions import SessionStore


def _session(identity_fixtures):
    import json

    fixtures = json.loads(identity_fixtures.read_text(encoding="utf-8"))
    store = SessionStore(fixtures, ttl_seconds=60, max_sessions=2)
    return store, store.create()


def test_uncertain_retry_retains_idempotency_and_correlation(identity_fixtures):
    _, session = _session(identity_fixtures)
    ledger = CommandLedger(max_commands_per_session=4)
    seen = []
    key = "intent-1"

    async def operation(idempotency_key, correlation_id):
        seen.append((idempotency_key, correlation_id))
        if len(seen) == 1:
            raise TransportError(
                service="registration",
                method="POST",
                code="TIMEOUT",
                message="The result is unknown.",
                correlation_id=correlation_id,
                outcome_unknown=True,
            )
        return "registered"

    async def exercise():
        with pytest.raises(TransportError):
            await ledger.execute(
                session,
                idempotency_key=key,
                payload={"activity_id": "activity-1"},
                operation=operation,
            )
        return await ledger.execute(
            session,
            idempotency_key=key,
            payload={"activity_id": "activity-1"},
            operation=operation,
        )

    outcome = asyncio.run(exercise())

    assert outcome.result == "registered"
    assert len(seen) == 2
    assert seen[0] == seen[1]


def test_concurrent_duplicate_commands_run_once(identity_fixtures):
    _, session = _session(identity_fixtures)
    ledger = CommandLedger(max_commands_per_session=4)
    calls = []

    async def operation(idempotency_key, correlation_id):
        calls.append((idempotency_key, correlation_id))
        await asyncio.sleep(0.01)
        return "created"

    async def exercise():
        return await asyncio.gather(
            *(
                ledger.execute(
                    session,
                    idempotency_key="same-intent",
                    payload={"title": "Synthetic event"},
                    operation=operation,
                )
                for _ in range(8)
            )
        )

    results = asyncio.run(exercise())

    assert len(calls) == 1
    assert {result.result for result in results} == {"created"}
    assert len({result.correlation_id for result in results}) == 1


def test_changed_payload_with_same_key_is_rejected(identity_fixtures):
    _, session = _session(identity_fixtures)
    ledger = CommandLedger(max_commands_per_session=4)

    async def operation(idempotency_key, correlation_id):
        return idempotency_key

    async def exercise():
        await ledger.execute(
            session,
            idempotency_key="same-intent",
            payload={"title": "First"},
            operation=operation,
        )
        with pytest.raises(CommandConflictError):
            await ledger.execute(
                session,
                idempotency_key="same-intent",
                payload={"title": "Changed"},
                operation=operation,
            )

    asyncio.run(exercise())


def test_persona_change_invalidates_prior_command_intent(identity_fixtures):
    store, session = _session(identity_fixtures)
    ledger = CommandLedger(max_commands_per_session=4)

    async def operation(idempotency_key, correlation_id):
        return "done"

    async def exercise():
        await ledger.execute(
            session,
            idempotency_key="persona-bound-intent",
            payload={"event_id": "event-1"},
            operation=operation,
        )
        store.set_persona(session, "participant")
        with pytest.raises(CommandConflictError):
            await ledger.execute(
                session,
                idempotency_key="persona-bound-intent",
                payload={"event_id": "event-1"},
                operation=operation,
            )

    asyncio.run(exercise())


def test_command_capacity_is_bounded(identity_fixtures):
    _, session = _session(identity_fixtures)
    ledger = CommandLedger(max_commands_per_session=1)

    async def operation(idempotency_key, correlation_id):
        return idempotency_key

    async def exercise():
        await ledger.execute(
            session,
            idempotency_key="first",
            payload={},
            operation=operation,
        )
        with pytest.raises(OverflowError):
            await ledger.execute(
                session,
                idempotency_key="second",
                payload={},
                operation=operation,
            )

    asyncio.run(exercise())
