import re

import httpx
import pytest
from fastapi.testclient import TestClient

import demo_web.app as app_module
from demo_web.app import create_app
from demo_web.clients import DemoClients


EVENT_ID = "10000000-0000-4000-8000-000000000001"
ACTIVITY_ID = "10000000-0000-4000-8000-000000000002"
REGISTRATION_ID = "10000000-0000-4000-8000-000000000003"
ORGANIZATION_ID = "10000000-0000-4000-8000-000000000004"
PARTICIPANT_ID = "10000000-0000-4000-8000-000000000005"
CORRELATION_ID = "10000000-0000-4000-8000-000000000006"


def _event():
    return {
        "id": EVENT_ID,
        "organizationId": ORGANIZATION_ID,
        "title": "Synthetic event",
        "description": "Checkout test fixture",
        "startsAt": "2027-01-02T10:00:00Z",
        "endsAt": "2027-01-02T12:00:00Z",
        "status": "PUBLISHED",
        "version": 1,
    }


def _activity(*, amount_minor=1250):
    return {
        "id": ACTIVITY_ID,
        "eventId": EVENT_ID,
        "organizationId": ORGANIZATION_ID,
        "title": "Paid workshop" if amount_minor else "Free workshop",
        "startsAt": "2027-01-02T10:30:00Z",
        "endsAt": "2027-01-02T11:30:00Z",
        "location": "Main hall",
        "responsible": "Demo organizer",
        "capacity": 4,
        "price": {"amountMinor": amount_minor, "currency": "BRL"},
        "version": 1,
        "capacityFrozen": False,
    }


def _registration(status="PENDING_PAYMENT"):
    return {
        "id": REGISTRATION_ID,
        "activityId": ACTIVITY_ID,
        "participantId": PARTICIPANT_ID,
        "organizationId": ORGANIZATION_ID,
        "status": status,
        "refundStatus": "NONE",
        "createdAt": "2027-01-01T10:00:00Z",
        "version": 1,
        "expiresAt": "2027-01-01T10:15:00Z" if status == "PENDING_PAYMENT" else None,
        "offerExpiresAt": None,
        "waitlistSequence": None,
        "price": {"amountMinor": 1999, "currency": "BRL"},
    }


def _error():
    return {
        "code": "SERVICE_UNAVAILABLE",
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


def _select_participant(client):
    initial = client.get("/session").json()
    changed = client.post(
        "/persona/participant",
        data={"csrf_token": initial["csrf_token"]},
        headers={"Origin": "http://testserver"},
        follow_redirects=False,
    )
    assert changed.status_code == 303
    return client.get("/session").json()["csrf_token"]


def _hidden_value(html, name):
    match = re.search(rf'name="{name}" value="([^"]+)"', html)
    assert match is not None, f"Missing hidden form value {name}"
    return match.group(1)


def _checkout_data(csrf_token, idempotency_key):
    return {
        "csrf_token": csrf_token,
        "idempotency_key": idempotency_key,
    }


def test_checkout_get_has_no_registration_side_effects_and_discloses_boundaries(
    monkeypatch, web_environment
):
    requests = []

    def respond(request):
        requests.append(request)
        if request.url.path == f"/activities/{ACTIVITY_ID}":
            return httpx.Response(200, json=_activity())
        if request.url.path == f"/events/{EVENT_ID}":
            return httpx.Response(200, json=_event())
        raise AssertionError(f"Unexpected service request: {request.method} {request.url}")

    with _test_client(monkeypatch, web_environment, respond) as client:
        _select_participant(client)
        response = client.get(f"/activities/{ACTIVITY_ID}/checkout")

    assert response.status_code == 200
    assert "Synthetic event" in response.text
    assert "Paid workshop" in response.text
    assert "BRL 12.50" in response.text
    assert "no card details" in response.text.lower()
    assert 'name="card_number"' not in response.text
    assert all(request.method == "GET" for request in requests)
    assert all(request.url.path != "/registrations" for request in requests)


@pytest.mark.parametrize(
    ("amount_minor", "expected_copy"),
    [
        (0, "No payment step is needed"),
        (1250, "Billing is simulated"),
    ],
)
def test_checkout_explains_free_and_paid_registration(
    monkeypatch, web_environment, amount_minor, expected_copy
):
    def respond(request):
        if request.url.path == f"/activities/{ACTIVITY_ID}":
            return httpx.Response(200, json=_activity(amount_minor=amount_minor))
        if request.url.path == f"/events/{EVENT_ID}":
            return httpx.Response(200, json=_event())
        raise AssertionError(f"Unexpected service request: {request.method} {request.url}")

    with _test_client(monkeypatch, web_environment, respond) as client:
        _select_participant(client)
        response = client.get(f"/activities/{ACTIVITY_ID}/checkout")

    assert response.status_code == 200
    assert expected_copy in response.text
    assert "Confirm registration" in response.text


def test_checkout_requires_participant_before_loading_service_data(monkeypatch, web_environment, sign_in):
    requests = []

    def respond(request):
        requests.append(request)
        raise AssertionError("Organizer checkout must not query service data.")

    with _test_client(monkeypatch, web_environment, respond) as client:
        sign_in(client, "organizer")
        response = client.get(f"/activities/{ACTIVITY_ID}/checkout", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/organizer"
    assert requests == []


def test_checkout_post_requires_csrf_before_registration_mutation(monkeypatch, web_environment):
    requests = []

    def respond(request):
        requests.append(request)
        if request.url.path == f"/activities/{ACTIVITY_ID}":
            return httpx.Response(200, json=_activity())
        if request.url.path == f"/events/{EVENT_ID}":
            return httpx.Response(200, json=_event())
        raise AssertionError("Registration must not be attempted without CSRF.")

    with _test_client(monkeypatch, web_environment, respond) as client:
        csrf_token = _select_participant(client)
        page = client.get(f"/activities/{ACTIVITY_ID}/checkout")
        key = _hidden_value(page.text, "idempotency_key")
        response = client.post(
            f"/activities/{ACTIVITY_ID}/checkout",
            data=_checkout_data(csrf_token, key),
            follow_redirects=False,
        )

    assert response.status_code == 403
    assert all(request.method == "GET" for request in requests)


def test_checkout_submits_only_activity_id_and_uses_backend_registration_price(
    monkeypatch, web_environment
):
    registration_requests = []

    def respond(request):
        if request.url.path == f"/activities/{ACTIVITY_ID}":
            return httpx.Response(200, json=_activity())
        if request.url.path == f"/events/{EVENT_ID}":
            return httpx.Response(200, json=_event())
        if request.url.path == "/registrations":
            registration_requests.append(request)
            assert request.method == "POST"
            assert request.read() == b'{"activityId":"' + ACTIVITY_ID.encode() + b'"}'
            return httpx.Response(201, json=_registration())
        raise AssertionError(f"Unexpected service request: {request.method} {request.url}")

    with _test_client(monkeypatch, web_environment, respond) as client:
        csrf_token = _select_participant(client)
        page = client.get(f"/activities/{ACTIVITY_ID}/checkout")
        key = _hidden_value(page.text, "idempotency_key")
        response = client.post(
            f"/activities/{ACTIVITY_ID}/checkout",
            data=_checkout_data(csrf_token, key),
            headers={"Origin": "http://testserver"},
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert response.headers["location"] == f"/registrations/{REGISTRATION_ID}"
    assert len(registration_requests) == 1
    assert "1999" not in page.text
    assert "synthetic-participant" not in page.text


def test_duplicate_checkout_submission_reuses_one_command(monkeypatch, web_environment):
    registration_requests = []

    def respond(request):
        if request.url.path == f"/activities/{ACTIVITY_ID}":
            return httpx.Response(200, json=_activity())
        if request.url.path == f"/events/{EVENT_ID}":
            return httpx.Response(200, json=_event())
        if request.url.path == "/registrations":
            registration_requests.append(request)
            return httpx.Response(201, json=_registration())
        raise AssertionError(f"Unexpected service request: {request.method} {request.url}")

    with _test_client(monkeypatch, web_environment, respond) as client:
        csrf_token = _select_participant(client)
        page = client.get(f"/activities/{ACTIVITY_ID}/checkout")
        key = _hidden_value(page.text, "idempotency_key")
        payload = _checkout_data(csrf_token, key)
        first = client.post(
            f"/activities/{ACTIVITY_ID}/checkout",
            data=payload,
            headers={"Origin": "http://testserver"},
            follow_redirects=False,
        )
        duplicate = client.post(
            f"/activities/{ACTIVITY_ID}/checkout",
            data=payload,
            headers={"Origin": "http://testserver"},
            follow_redirects=False,
        )

    assert first.status_code == duplicate.status_code == 303
    assert first.headers["location"] == duplicate.headers["location"]
    assert len(registration_requests) == 1
    assert registration_requests[0].headers["idempotency-key"] == key


def test_unknown_checkout_result_can_retry_same_key_and_correlation(monkeypatch, web_environment):
    registration_requests = []

    def respond(request):
        if request.url.path == f"/activities/{ACTIVITY_ID}":
            return httpx.Response(200, json=_activity())
        if request.url.path == f"/events/{EVENT_ID}":
            return httpx.Response(200, json=_event())
        if request.url.path == "/registrations":
            registration_requests.append(request)
            if len(registration_requests) == 1:
                raise httpx.ReadTimeout("connection timed out")
            return httpx.Response(201, json=_registration())
        raise AssertionError(f"Unexpected service request: {request.method} {request.url}")

    with _test_client(monkeypatch, web_environment, respond) as client:
        csrf_token = _select_participant(client)
        page = client.get(f"/activities/{ACTIVITY_ID}/checkout")
        key = _hidden_value(page.text, "idempotency_key")
        first = client.post(
            f"/activities/{ACTIVITY_ID}/checkout",
            data=_checkout_data(csrf_token, key),
            headers={"Origin": "http://testserver"},
            follow_redirects=False,
        )
        retry_key = _hidden_value(first.text, "idempotency_key")
        retry_csrf = _hidden_value(first.text, "csrf_token")
        retry = client.post(
            f"/activities/{ACTIVITY_ID}/checkout",
            data=_checkout_data(retry_csrf, retry_key),
            headers={"Origin": "http://testserver"},
            follow_redirects=False,
        )

    assert first.status_code == 503
    assert "result is unknown" in first.text.lower()
    assert retry.status_code == 303
    assert retry_key == key
    assert len(registration_requests) == 2
    assert registration_requests[0].headers["idempotency-key"] == key
    assert registration_requests[1].headers["idempotency-key"] == key
    assert (
        registration_requests[0].headers["x-correlation-id"]
        == registration_requests[1].headers["x-correlation-id"]
    )


@pytest.mark.parametrize(
    "status",
    ["PENDING_PAYMENT", "REFUSED"],
)
def test_checkout_redirects_to_backend_pending_or_refused_registration(
    monkeypatch, web_environment, status
):
    def respond(request):
        if request.url.path == f"/activities/{ACTIVITY_ID}":
            return httpx.Response(200, json=_activity())
        if request.url.path == f"/events/{EVENT_ID}":
            return httpx.Response(200, json=_event())
        if request.url.path == "/registrations":
            return httpx.Response(201, json=_registration(status))
        raise AssertionError(f"Unexpected service request: {request.method} {request.url}")

    with _test_client(monkeypatch, web_environment, respond) as client:
        csrf_token = _select_participant(client)
        page = client.get(f"/activities/{ACTIVITY_ID}/checkout")
        key = _hidden_value(page.text, "idempotency_key")
        response = client.post(
            f"/activities/{ACTIVITY_ID}/checkout",
            data=_checkout_data(csrf_token, key),
            headers={"Origin": "http://testserver"},
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert response.headers["location"] == f"/registrations/{REGISTRATION_ID}"
