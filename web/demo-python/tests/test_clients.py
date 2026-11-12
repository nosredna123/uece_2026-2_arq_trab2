import asyncio
import json
from uuid import UUID

import httpx

from demo_web.clients import MAX_CONNECTIONS, DemoClients
from demo_web.models import (
    ActivityInput,
    Certificate,
    CheckIn,
    EventInput,
    Report,
)


EVENT_ID = "10000000-0000-4000-8000-000000000001"
ACTIVITY_ID = "10000000-0000-4000-8000-000000000002"
REGISTRATION_ID = "10000000-0000-4000-8000-000000000003"
ORGANIZATION_ID = "10000000-0000-4000-8000-000000000004"
PARTICIPANT_ID = "10000000-0000-4000-8000-000000000005"
CORRELATION_ID = "10000000-0000-4000-8000-000000000006"
IDEMPOTENCY_KEY = "10000000-0000-4000-8000-000000000007"
TOKEN = "synthetic-token-never-record"

EVENT = {
    "id": EVENT_ID,
    "organizationId": ORGANIZATION_ID,
    "title": "Synthetic event",
    "description": "Presentation fixture",
    "startsAt": "2027-01-01T10:00:00Z",
    "endsAt": "2027-01-01T12:00:00Z",
    "status": "PUBLISHED",
    "version": 1,
}
ACTIVITY = {
    "id": ACTIVITY_ID,
    "eventId": EVENT_ID,
    "organizationId": ORGANIZATION_ID,
    "title": "Synthetic activity",
    "startsAt": "2027-01-01T10:00:00Z",
    "endsAt": "2027-01-01T11:00:00Z",
    "location": "Room A",
    "responsible": "Synthetic organizer",
    "capacity": 1,
    "price": {"amountMinor": 0, "currency": "USD"},
    "version": 1,
    "capacityFrozen": False,
}
REGISTRATION = {
    "id": REGISTRATION_ID,
    "activityId": ACTIVITY_ID,
    "participantId": PARTICIPANT_ID,
    "organizationId": ORGANIZATION_ID,
    "status": "CONFIRMED",
    "refundStatus": "NONE",
    "createdAt": "2027-01-01T09:00:00Z",
    "version": 1,
}
AVAILABILITY = {
    "activityId": ACTIVITY_ID,
    "capacity": 1,
    "occupied": 0,
    "available": 1,
    "asOf": "2027-01-01T09:00:00Z",
}
CHECK_IN = {
    "registrationId": REGISTRATION_ID,
    "participantId": PARTICIPANT_ID,
    "checkedInAt": "2027-01-01T10:00:00Z",
}
CERTIFICATE = {
    "id": "10000000-0000-4000-8000-000000000008",
    "registrationId": REGISTRATION_ID,
    "participantId": PARTICIPANT_ID,
    "activityId": ACTIVITY_ID,
    "issuedAt": "2027-01-01T12:00:00Z",
}
REPORT = {
    "organizationId": ORGANIZATION_ID,
    "activityId": ACTIVITY_ID,
    "confirmed": 1,
    "pendingPayment": 0,
    "offered": 0,
    "waiting": 0,
    "occupied": 1,
    "checkedIn": 1,
    "certificates": 1,
    "paymentsApproved": 0,
    "paymentsRefused": 0,
    "refundsApproved": 0,
    "projectedAt": "2027-01-01T12:01:00Z",
}


def _activity_input():
    return ActivityInput.model_validate(
        {
            "title": "Synthetic activity",
            "startsAt": "2027-01-01T10:00:00Z",
            "endsAt": "2027-01-01T11:00:00Z",
            "location": "Room A",
            "responsible": "Synthetic organizer",
            "capacity": 1,
            "price": {"amountMinor": 0, "currency": "USD"},
        }
    )


def _event_input():
    return EventInput.model_validate(
        {
            "organizationId": ORGANIZATION_ID,
            "title": "Synthetic event",
            "description": "Presentation fixture",
            "startsAt": "2027-01-01T10:00:00Z",
            "endsAt": "2027-01-01T12:00:00Z",
        }
    )


def test_operation_clients_follow_contracts_and_headers(web_environment):
    seen = []

    def respond(request):
        seen.append(request)
        assert request.extensions["timeout"]["read"] == 5
        path = request.url.path
        if path == "/events" and request.method == "GET":
            return httpx.Response(200, json=[EVENT])
        if path == "/events" and request.method == "POST":
            body = json.loads(request.content)
            assert body["organizationId"] == ORGANIZATION_ID
            assert set(body) == {"organizationId", "title", "description", "startsAt", "endsAt"}
            return httpx.Response(201, json={**EVENT, "status": "DRAFT"})
        if path == f"/events/{EVENT_ID}" and request.method == "GET":
            return httpx.Response(200, json=EVENT)
        if path == f"/events/{EVENT_ID}/activities" and request.method == "GET":
            return httpx.Response(200, json=[ACTIVITY])
        if path == f"/events/{EVENT_ID}/activities" and request.method == "POST":
            body = json.loads(request.content)
            assert body["title"] == "Synthetic activity"
            assert body["price"] == {"amountMinor": 0, "currency": "USD"}
            assert "participantId" not in body
            return httpx.Response(201, json=ACTIVITY)
        if path == f"/events/{EVENT_ID}/publish":
            return httpx.Response(200, json=EVENT)
        if path == f"/activities/{ACTIVITY_ID}" and request.method == "GET":
            return httpx.Response(200, json=ACTIVITY)
        if path == f"/activities/{ACTIVITY_ID}/availability":
            return httpx.Response(200, json=AVAILABILITY)
        if path == "/registrations" and request.method == "POST":
            assert json.loads(request.content) == {"activityId": ACTIVITY_ID}
            return httpx.Response(201, json=REGISTRATION)
        if path == "/registrations" and request.method == "GET":
            return httpx.Response(200, json=[REGISTRATION])
        if path == f"/registrations/{REGISTRATION_ID}":
            return httpx.Response(200, json=REGISTRATION)
        if path == f"/registrations/{REGISTRATION_ID}/cancel":
            return httpx.Response(202, json={**REGISTRATION, "refundStatus": "PENDING"})
        if path == f"/registrations/{REGISTRATION_ID}/accept-offer":
            return httpx.Response(200, json=REGISTRATION)
        if path == f"/registrations/{REGISTRATION_ID}/check-in":
            return httpx.Response(200, json=CHECK_IN)
        if path == f"/registrations/{REGISTRATION_ID}/certificate":
            if request.method == "POST":
                return httpx.Response(200, json=CERTIFICATE)
            return httpx.Response(200, json=CERTIFICATE)
        if path == f"/organizations/{ORGANIZATION_ID}/reports":
            return httpx.Response(200, json=[REPORT])
        raise AssertionError(f"Unexpected operation: {request.method} {path}")

    async def exercise_clients():
        transport = httpx.MockTransport(respond)
        async with DemoClients(_settings(web_environment), transport=transport) as clients:
            assert len(await clients.list_events(search="synthetic")) == 1
            assert (await clients.get_event(UUID(EVENT_ID))).id == UUID(EVENT_ID)
            assert len(await clients.list_activities(UUID(EVENT_ID))) == 1
            assert (await clients.get_activity(UUID(ACTIVITY_ID))).id == UUID(ACTIVITY_ID)
            assert (
                await clients.create_event(
                    _event_input(),
                    token=TOKEN,
                    idempotency_key=IDEMPOTENCY_KEY,
                    correlation_id=CORRELATION_ID,
                )
            ).status == "DRAFT"
            assert (
                await clients.create_activity(
                    UUID(EVENT_ID),
                    _activity_input(),
                    token=TOKEN,
                    idempotency_key=IDEMPOTENCY_KEY,
                    correlation_id=CORRELATION_ID,
                )
            ).capacity == 1
            assert (
                await clients.publish_event(
                    UUID(EVENT_ID),
                    token=TOKEN,
                    idempotency_key=IDEMPOTENCY_KEY,
                    correlation_id=CORRELATION_ID,
                )
            ).status == "PUBLISHED"
            assert (await clients.get_availability(UUID(ACTIVITY_ID))).available == 1
            assert (
                await clients.request_registration(
                    UUID(ACTIVITY_ID),
                    token=TOKEN,
                    idempotency_key=IDEMPOTENCY_KEY,
                    correlation_id=CORRELATION_ID,
                )
            ).status == "CONFIRMED"
            assert len(await clients.list_registrations(token=TOKEN)) == 1
            assert (await clients.get_registration(UUID(REGISTRATION_ID), token=TOKEN)).id == UUID(
                REGISTRATION_ID
            )
            assert (
                await clients.cancel_registration(
                    UUID(REGISTRATION_ID),
                    token=TOKEN,
                    idempotency_key=IDEMPOTENCY_KEY,
                    correlation_id=CORRELATION_ID,
                )
            ).refund_status == "PENDING"
            assert (
                await clients.accept_offer(
                    UUID(REGISTRATION_ID),
                    token=TOKEN,
                    idempotency_key=IDEMPOTENCY_KEY,
                    correlation_id=CORRELATION_ID,
                )
            ).status == "CONFIRMED"
            assert isinstance(
                await clients.check_in(
                    UUID(REGISTRATION_ID),
                    token=TOKEN,
                    idempotency_key=IDEMPOTENCY_KEY,
                    correlation_id=CORRELATION_ID,
                ),
                CheckIn,
            )
            assert isinstance(
                await clients.issue_certificate(
                    UUID(REGISTRATION_ID),
                    token=TOKEN,
                    idempotency_key=IDEMPOTENCY_KEY,
                    correlation_id=CORRELATION_ID,
                ),
                Certificate,
            )
            assert isinstance(
                await clients.get_certificate(UUID(REGISTRATION_ID), token=TOKEN),
                Certificate,
            )
            assert isinstance(
                (
                    await clients.get_reports(
                        UUID(ORGANIZATION_ID),
                        token=TOKEN,
                        activity_id=UUID(ACTIVITY_ID),
                        limit=10,
                        offset=2,
                    )
                )[0],
                Report,
            )
            return clients.history

    history = asyncio.run(exercise_clients())
    assert len(seen) == 17
    for request in seen:
        assert UUID(request.headers["X-Correlation-ID"])
    protected = [
        request
        for request in seen
        if request.url.path.startswith(("/registrations", "/organizations"))
        or request.method == "POST"
    ]
    assert all(request.headers["Authorization"] == f"Bearer {TOKEN}" for request in protected)
    assert all(
        request.headers["Idempotency-Key"] == IDEMPOTENCY_KEY
        for request in seen
        if request.method == "POST"
    )
    public = [
        request
        for request in seen
        if request.method == "GET"
        and request.url.path
        in (
            "/events",
            f"/events/{EVENT_ID}",
            f"/events/{EVENT_ID}/activities",
            f"/activities/{ACTIVITY_ID}",
            f"/activities/{ACTIVITY_ID}/availability",
        )
    ]
    assert all("Authorization" not in request.headers for request in public)
    assert seen[0].url.params["q"] == "synthetic"
    assert dict(seen[-1].url.params) == {
        "limit": "10",
        "offset": "2",
        "activityId": ACTIVITY_ID,
    }
    assert all(key not in repr(history) for key in (TOKEN, "Authorization", IDEMPOTENCY_KEY))


def test_request_history_is_bounded_and_contains_no_request_secrets(web_environment):
    requests_seen = []

    def respond(request):
        requests_seen.append(request)
        return httpx.Response(200, json=[])

    async def exercise_clients():
        async with DemoClients(
            _settings(web_environment), transport=httpx.MockTransport(respond)
        ) as clients:
            for _ in range(25):
                await clients.list_events()
            return clients.history

    history = asyncio.run(exercise_clients())

    assert len(history) == 20
    assert all(entry.service == "event-management" for entry in history)
    assert all(entry.method == "GET" and entry.status == 200 for entry in history)
    assert TOKEN not in repr(history)
    assert IDEMPOTENCY_KEY not in repr(history)
    assert all(
        not hasattr(entry, "path") and not hasattr(entry, "request_body") for entry in history
    )


def test_observer_sees_every_finished_request(web_environment):
    def respond(request):
        return httpx.Response(200, json=[])

    seen = []

    async def exercise_clients():
        async with DemoClients(
            _settings(web_environment), transport=httpx.MockTransport(respond)
        ) as clients:
            clients.observer = seen.append
            await clients.list_events()
            await clients.list_events()

    asyncio.run(exercise_clients())

    assert [(entry.service, entry.method, entry.status) for entry in seen] == [
        ("event-management", "GET", 200)
    ] * 2


def test_request_history_isolated_per_session_and_session_count_is_bounded(web_environment):
    def respond(request):
        return httpx.Response(200, json=[])

    async def exercise_clients():
        settings = _settings({**web_environment, "DEMO_WEB_MAX_SESSIONS": "2"})
        async with DemoClients(settings, transport=httpx.MockTransport(respond)) as clients:
            await clients.list_events(session_id="session-one")
            await clients.list_events(session_id="session-two")
            await clients.list_events(session_id="session-three")
            return (
                clients.history_for("session-one"),
                clients.history_for("session-two"),
                clients.history_for("session-three"),
                clients.history,
            )

    first, second, third, unscoped = asyncio.run(exercise_clients())

    assert first == ()
    assert len(second) == 1
    assert len(third) == 1
    assert unscoped == ()


def test_rejects_invalid_identifiers_and_query_bounds_before_network(web_environment):
    def fail_if_called(request):
        raise AssertionError("invalid input must not reach a service")

    async def exercise_clients():
        async with DemoClients(
            _settings(web_environment), transport=httpx.MockTransport(fail_if_called)
        ) as clients:
            try:
                await clients.get_event("not-a-uuid")
            except ValueError as error:
                assert "UUID" in str(error)
            else:
                raise AssertionError("invalid identifier was accepted")
            try:
                await clients.list_events(limit=101)
            except ValueError as error:
                assert "limit" in str(error)
            else:
                raise AssertionError("invalid query bound was accepted")
            try:
                await clients.list_events(session_id="\r\ninvalid")
            except ValueError as error:
                assert "session" in str(error)
            else:
                raise AssertionError("invalid session identifier was accepted")

    asyncio.run(exercise_clients())


def test_scenario_clients_queue_requests_outside_the_connection_pool(web_environment):
    in_flight = 0
    peak = 0

    async def respond(request):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.001)
        in_flight -= 1
        return httpx.Response(200, json=[])

    async def exercise_clients():
        async with DemoClients(
            _settings(web_environment),
            transport=httpx.MockTransport(respond),
            wait_for_connection=True,
        ) as clients:
            await asyncio.gather(*(clients.list_events() for _ in range(200)))

    asyncio.run(exercise_clients())

    assert peak == MAX_CONNECTIONS


def test_scenario_clients_honour_a_custom_connection_limit(web_environment):
    in_flight = 0
    peak = 0

    async def respond(request):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.001)
        in_flight -= 1
        return httpx.Response(200, json=[])

    async def exercise_clients():
        async with DemoClients(
            _settings(web_environment),
            transport=httpx.MockTransport(respond),
            wait_for_connection=True,
            max_connections=50,
        ) as clients:
            await asyncio.gather(*(clients.list_events() for _ in range(200)))

    asyncio.run(exercise_clients())

    assert peak == 50


def _settings(environment):
    from demo_web.settings import Settings

    return Settings.from_environment(environment)
