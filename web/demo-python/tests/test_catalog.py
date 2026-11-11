from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

import demo_web.app as app_module
from demo_web.app import create_app
from demo_web.clients import DemoClients
from demo_web.models import Money
from demo_web.presentation import format_money


EVENT_ID = "10000000-0000-4000-8000-000000000001"
ACTIVITY_ONE = "10000000-0000-4000-8000-000000000002"
ACTIVITY_TWO = "10000000-0000-4000-8000-000000000003"
ORGANIZATION_ID = "10000000-0000-4000-8000-000000000004"
CORRELATION_ID = "10000000-0000-4000-8000-000000000005"


def _event(
    event_id=EVENT_ID,
    title="Synthetic event",
    status="PUBLISHED",
):
    return {
        "id": event_id,
        "organizationId": ORGANIZATION_ID,
        "title": title,
        "description": "Catalog test fixture",
        "startsAt": "2027-01-02T10:00:00Z",
        "endsAt": "2027-01-02T12:00:00Z",
        "status": status,
        "version": 1,
    }


def _activity(activity_id, title, *, amount_minor, currency="BRL"):
    return {
        "id": activity_id,
        "eventId": EVENT_ID,
        "organizationId": ORGANIZATION_ID,
        "title": title,
        "startsAt": "2027-01-02T10:30:00Z",
        "endsAt": "2027-01-02T11:30:00Z",
        "location": "Main hall",
        "responsible": "Demo organizer",
        "capacity": 4,
        "price": {"amountMinor": amount_minor, "currency": currency},
        "version": 1,
        "capacityFrozen": False,
    }


def _error(code="SERVICE_UNAVAILABLE"):
    return {
        "code": code,
        "message": "A sanitized test dependency error.",
        "correlationId": CORRELATION_ID,
    }


def _test_client(monkeypatch, web_environment, handler):
    monkeypatch.setattr(
        app_module,
        "DemoClients",
        lambda settings: DemoClients(
            settings,
            transport=httpx.MockTransport(handler),
        ),
    )
    return TestClient(create_app(environ=web_environment))


def test_catalog_search_pagination_and_no_registration_side_effect(monkeypatch, web_environment, sign_in):
    requests = []
    events = [
        _event("10000000-0000-4000-8000-000000000011", "First event"),
        _event("10000000-0000-4000-8000-000000000012", "Second event"),
        _event("10000000-0000-4000-8000-000000000013", "Third event"),
        _event("10000000-0000-4000-8000-000000000014", "Fourth event"),
    ]

    def respond(request):
        requests.append(request)
        assert request.url.path == "/events"
        assert request.method == "GET"
        assert request.url.params["q"] == "music"
        limit = int(request.url.params["limit"])
        offset = int(request.url.params["offset"])
        return httpx.Response(200, json=events[offset : offset + limit])

    with _test_client(monkeypatch, web_environment, respond) as client:
        sign_in(client)
        response = client.get("/events?q=music&limit=2&offset=2")

    assert response.status_code == 200
    assert "Third event" in response.text
    assert "Fourth event" in response.text
    assert "First event" not in response.text
    assert "Previous" in response.text
    assert "Next" in response.text
    assert len(requests) == 1
    assert all(request.method == "GET" for request in requests)
    assert all(request.url.path != "/registrations" for request in requests)


def test_catalog_escapes_backend_content_and_filters_unpublished_events(
    monkeypatch, web_environment, sign_in
):
    events = [
        _event(title='<script>alert("unsafe")</script>'),
        _event(
            event_id="10000000-0000-4000-8000-000000000015",
            title="Draft event",
            status="DRAFT",
        ),
    ]

    def respond(request):
        return httpx.Response(200, json=events)

    with _test_client(monkeypatch, web_environment, respond) as client:
        sign_in(client)
        response = client.get("/events")

    assert response.status_code == 200
    assert "<script>" not in response.text
    assert "&lt;script&gt;" in response.text
    assert "Draft event" not in response.text
    assert "No published events match" not in response.text


def test_event_detail_formats_schedule_and_minor_units_and_labels_stale_or_unavailable(
    monkeypatch, web_environment, sign_in
):
    now = datetime.now(timezone.utc)
    stale_as_of = (now - timedelta(minutes=3)).isoformat()
    activity_one = _activity(ACTIVITY_ONE, "Paid workshop", amount_minor=1250)
    activity_two = _activity(ACTIVITY_TWO, "Free workshop", amount_minor=0)
    availability_requests = []

    def respond(request):
        if request.url.path == f"/events/{EVENT_ID}":
            return httpx.Response(200, json=_event())
        if request.url.path == f"/events/{EVENT_ID}/activities":
            return httpx.Response(200, json=[activity_one, activity_two])
        if request.url.path == f"/activities/{ACTIVITY_ONE}/availability":
            availability_requests.append(request)
            return httpx.Response(
                200,
                json={
                    "activityId": ACTIVITY_ONE,
                    "capacity": 4,
                    "occupied": 4,
                    "available": 0,
                    "asOf": stale_as_of,
                },
            )
        if request.url.path == f"/activities/{ACTIVITY_TWO}/availability":
            availability_requests.append(request)
            return httpx.Response(503, json=_error("AVAILABILITY_DOWN"))
        raise AssertionError(f"Unexpected service request: {request.method} {request.url}")

    with _test_client(monkeypatch, web_environment, respond) as client:
        sign_in(client)
        response = client.get(f"/events/{EVENT_ID}")

    assert response.status_code == 200
    assert "Synthetic event" in response.text
    assert "Paid workshop" in response.text
    assert "Free workshop" in response.text
    assert "BRL 12.50" in response.text
    assert "Free" in response.text
    assert "Jan 02, 2027 10:30 UTC" in response.text
    assert "Available: 0" in response.text
    assert "may be outdated" in response.text
    assert "Availability unavailable" in response.text
    unavailable_card = response.text.split(f'id="activity-{ACTIVITY_TWO}"', 1)[1]
    unavailable_card = unavailable_card.split("</article>", 1)[0]
    assert "Available:" not in unavailable_card
    assert len(availability_requests) == 2
    assert all(request.method == "GET" for request in availability_requests)


def test_price_formatting_preserves_large_integer_minor_units():
    price = Money(amountMinor=9007199254740991, currency="BRL")

    assert format_money(price) == "BRL 90,071,992,547,409.91"


def test_event_detail_fetches_availability_only_for_displayed_activity_page(
    monkeypatch, web_environment, sign_in
):
    activity_one = _activity(ACTIVITY_ONE, "First page", amount_minor=1000)
    activity_two = _activity(ACTIVITY_TWO, "Second page", amount_minor=2000)
    activity_requests = []
    availability_requests = []

    def respond(request):
        if request.url.path == f"/events/{EVENT_ID}":
            return httpx.Response(200, json=_event())
        if request.url.path == f"/events/{EVENT_ID}/activities":
            activity_requests.append(request)
            limit = int(request.url.params["limit"])
            offset = int(request.url.params["offset"])
            return httpx.Response(
                200,
                json=[activity_one, activity_two][offset : offset + limit],
            )
        if request.url.path.startswith("/activities/") and request.url.path.endswith(
            "/availability"
        ):
            availability_requests.append(request)
            activity_id = request.url.path.split("/")[2]
            return httpx.Response(
                200,
                json={
                    "activityId": activity_id,
                    "capacity": 4,
                    "occupied": 0,
                    "available": 4,
                    "asOf": datetime.now(timezone.utc).isoformat(),
                },
            )
        raise AssertionError(f"Unexpected service request: {request.method} {request.url}")

    with _test_client(monkeypatch, web_environment, respond) as client:
        sign_in(client)
        response = client.get(f"/events/{EVENT_ID}?limit=1&offset=1")

    assert response.status_code == 200
    assert "Second page" in response.text
    assert "First page" not in response.text
    assert len(activity_requests) == 1
    assert activity_requests[0].url.params["limit"] == "1"
    assert activity_requests[0].url.params["offset"] == "1"
    assert len(availability_requests) == 1
    assert availability_requests[0].url.path == (f"/activities/{ACTIVITY_TWO}/availability")


def test_unpublished_event_is_not_visible_and_keeps_not_found_status(monkeypatch, web_environment, sign_in):
    def respond(request):
        return httpx.Response(404, json=_error("NOT_FOUND"))

    with _test_client(monkeypatch, web_environment, respond) as client:
        sign_in(client)
        response = client.get(
            f"/events/{EVENT_ID}",
            headers={"Accept": "text/html"},
        )

    assert response.status_code == 404
    assert "The requested page was not found." in response.text


def test_activity_program_failure_does_not_hide_valid_event(monkeypatch, web_environment, sign_in):
    def respond(request):
        if request.url.path == f"/events/{EVENT_ID}":
            return httpx.Response(200, json=_event())
        if request.url.path == f"/events/{EVENT_ID}/activities":
            return httpx.Response(503, json=_error("PROGRAM_DOWN"))
        raise AssertionError(f"Unexpected service request: {request.method} {request.url}")

    with _test_client(monkeypatch, web_environment, respond) as client:
        sign_in(client)
        response = client.get(f"/events/{EVENT_ID}")

    assert response.status_code == 200
    assert "Synthetic event" in response.text
    assert "Activities are temporarily unavailable" in response.text
    assert "PROGRAM_DOWN" in response.text


def test_catalog_service_failure_is_not_rendered_as_an_empty_catalog(monkeypatch, web_environment, sign_in):
    def respond(request):
        return httpx.Response(503, json=_error("CATALOG_DOWN"))

    with _test_client(monkeypatch, web_environment, respond) as client:
        sign_in(client)
        response = client.get("/events")

    assert response.status_code == 503
    assert "CATALOG_DOWN" in response.text
    assert "No published events match" not in response.text


@pytest.mark.parametrize(
    "query",
    [
        {"limit": 101},
        {"offset": 10001},
        {"q": "x" * 201},
    ],
)
def test_catalog_query_bounds_are_validated_before_service_calls(
    monkeypatch, web_environment, query, sign_in
):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json=[])

    with _test_client(monkeypatch, web_environment, respond) as client:
        sign_in(client)
        response = client.get("/events", params=query)

    assert response.status_code == 422
    assert requests == []
