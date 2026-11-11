import re
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

import demo_web.app as app_module
from demo_web.app import create_app
from demo_web.clients import DemoClients


REGISTRATION_ID = "10000000-0000-4000-8000-000000000001"
ACTIVITY_ID = "10000000-0000-4000-8000-000000000002"
ORGANIZATION_ID = "10000000-0000-4000-8000-000000000003"
PARTICIPANT_ID = "10000000-0000-4000-8000-000000000004"
CORRELATION_ID = "10000000-0000-4000-8000-000000000005"


def _registration(
    status="PENDING_PAYMENT",
    *,
    refund_status="NONE",
    registration_id=REGISTRATION_ID,
):
    now = datetime.now(timezone.utc)
    return {
        "id": registration_id,
        "activityId": ACTIVITY_ID,
        "participantId": PARTICIPANT_ID,
        "organizationId": ORGANIZATION_ID,
        "status": status,
        "refundStatus": refund_status,
        "createdAt": (now - timedelta(minutes=2)).isoformat(),
        "version": 4,
        "expiresAt": (now + timedelta(minutes=8)).isoformat()
        if status == "PENDING_PAYMENT"
        else None,
        "offerExpiresAt": (now + timedelta(minutes=4)).isoformat() if status == "OFFERED" else None,
        "waitlistSequence": 3 if status == "WAITLISTED" else None,
        "price": {"amountMinor": 2500, "currency": "BRL"},
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


def _action_fields(html, path):
    form = re.search(
        rf'<form[^>]+action="{re.escape(path)}"[^>]*>(.*?)</form>',
        html,
        re.DOTALL,
    )
    assert form is not None, f"Missing form for {path}"
    fields = {
        name: value
        for name, value in re.findall(
            r'<input type="hidden" name="([^"]+)" value="([^"]*)">',
            form.group(1),
        )
    }
    return fields


@pytest.mark.parametrize(
    "status",
    [
        "WAITLISTED",
        "OFFERED",
        "PENDING_PAYMENT",
        "CONFIRMED",
        "REFUSED",
        "EXPIRED",
        "CANCELLED",
    ],
)
def test_registration_detail_renders_each_backend_state(monkeypatch, web_environment, status):
    def respond(request):
        if request.url.path == f"/registrations/{REGISTRATION_ID}":
            return httpx.Response(200, json=_registration(status))
        raise AssertionError(f"Unexpected service request: {request.method} {request.url}")

    with _test_client(monkeypatch, web_environment, respond) as client:
        _select_participant(client)
        response = client.get(f"/registrations/{REGISTRATION_ID}")

    assert response.status_code == 200
    assert status in response.text
    assert "LIVE SERVICE" in response.text
    assert "Last observed" in response.text
    assert "registration-position" not in response.text


def test_refund_pending_keeps_registration_confirmed_and_occupied(monkeypatch, web_environment):
    def respond(request):
        if request.url.path == f"/registrations/{REGISTRATION_ID}":
            return httpx.Response(
                200,
                json=_registration("CONFIRMED", refund_status="PENDING"),
            )
        raise AssertionError(f"Unexpected service request: {request.method} {request.url}")

    with _test_client(monkeypatch, web_environment, respond) as client:
        _select_participant(client)
        response = client.get(f"/registrations/{REGISTRATION_ID}")

    assert response.status_code == 200
    assert "CONFIRMED" in response.text
    assert "Refund pending" in response.text
    assert "still occupies capacity" in response.text


def test_offer_deadline_and_accept_action_are_backend_derived(monkeypatch, web_environment):
    def respond(request):
        if request.url.path == f"/registrations/{REGISTRATION_ID}":
            return httpx.Response(200, json=_registration("OFFERED"))
        raise AssertionError(f"Unexpected service request: {request.method} {request.url}")

    with _test_client(monkeypatch, web_environment, respond) as client:
        _select_participant(client)
        response = client.get(f"/registrations/{REGISTRATION_ID}")

    assert response.status_code == 200
    assert "Offer expires" in response.text
    assert "Accept offer" in response.text
    assert "accept-offer" in response.text
    assert "waitlistSequence" not in response.text
    assert "position #" not in response.text.lower()


def test_accept_offer_uses_registration_command_and_redirects(monkeypatch, web_environment):
    accepted_requests = []

    def respond(request):
        if request.url.path == f"/registrations/{REGISTRATION_ID}":
            return httpx.Response(200, json=_registration("OFFERED"))
        if request.url.path == f"/registrations/{REGISTRATION_ID}/accept-offer":
            accepted_requests.append(request)
            return httpx.Response(
                200,
                json=_registration("PENDING_PAYMENT"),
            )
        raise AssertionError(f"Unexpected service request: {request.method} {request.url}")

    with _test_client(monkeypatch, web_environment, respond) as client:
        csrf_token = _select_participant(client)
        page = client.get(f"/registrations/{REGISTRATION_ID}")
        path = f"/registrations/{REGISTRATION_ID}/accept-offer"
        fields = _action_fields(page.text, path)
        response = client.post(
            path,
            data=fields,
            headers={"Origin": "http://testserver"},
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert response.headers["location"] == f"/registrations/{REGISTRATION_ID}"
    assert len(accepted_requests) == 1
    assert accepted_requests[0].headers["idempotency-key"] == fields["idempotency_key"]
    assert fields["csrf_token"] == csrf_token


def test_registration_mutation_requires_csrf_before_service_command(monkeypatch, web_environment):
    requests = []

    def respond(request):
        requests.append(request)
        if request.url.path == f"/registrations/{REGISTRATION_ID}":
            return httpx.Response(200, json=_registration("CONFIRMED"))
        raise AssertionError("A mutation must not reach Registration without CSRF.")

    with _test_client(monkeypatch, web_environment, respond) as client:
        _select_participant(client)
        page = client.get(f"/registrations/{REGISTRATION_ID}")
        fields = _action_fields(page.text, f"/registrations/{REGISTRATION_ID}/cancel")
        response = client.post(
            f"/registrations/{REGISTRATION_ID}/cancel",
            data={"idempotency_key": fields["idempotency_key"]},
            follow_redirects=False,
        )

    assert response.status_code == 403
    assert len(requests) == 1
    assert requests[0].method == "GET"


def test_registrations_list_uses_backend_scoped_participant_endpoint(monkeypatch, web_environment):
    def respond(request):
        if request.url.path == "/registrations":
            assert request.method == "GET"
            assert request.url.params["limit"] == "20"
            assert request.url.params["offset"] == "0"
            assert request.headers["authorization"] == "Bearer synthetic-participant"
            return httpx.Response(200, json=[_registration("CONFIRMED")])
        raise AssertionError(f"Unexpected service request: {request.method} {request.url}")

    with _test_client(monkeypatch, web_environment, respond) as client:
        _select_participant(client)
        response = client.get("/registrations")

    assert response.status_code == 200
    assert "My registrations" in response.text
    assert REGISTRATION_ID in response.text
    assert "CONFIRMED" in response.text


def test_foreign_registration_is_concealed_as_not_found(monkeypatch, web_environment):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(404, json=_error("REGISTRATION_NOT_FOUND"))

    with _test_client(monkeypatch, web_environment, respond) as client:
        _select_participant(client)
        response = client.get(f"/registrations/{REGISTRATION_ID}")

    assert response.status_code == 404
    assert "not found" in response.text.lower()
    assert REGISTRATION_ID not in response.text
    assert len(requests) == 1
    assert requests[0].method == "GET"


def test_pause_control_and_persona_version_are_wired_for_polling(monkeypatch, web_environment):
    def respond(request):
        if request.url.path == f"/registrations/{REGISTRATION_ID}":
            return httpx.Response(200, json=_registration("PENDING_PAYMENT"))
        if request.url.path == "/activities/never-called":
            raise AssertionError("Unexpected activity request")
        raise AssertionError(f"Unexpected service request: {request.method} {request.url}")

    with _test_client(monkeypatch, web_environment, respond) as client:
        _select_participant(client)
        response = client.get(f"/registrations/{REGISTRATION_ID}")
        script = client.get("/static/status.js")

    assert response.status_code == 200
    assert "Pause updates" in response.text
    assert "persona-version" in response.text
    assert script.status_code == 200
    assert "pagehide" in script.text
    assert "paused" in script.text.lower()
    assert "Showing the last observed registration" in script.text


@pytest.mark.parametrize(
    ("status", "refund_status", "expected_polling"),
    [
        ("WAITLISTED", "NONE", True),
        ("OFFERED", "NONE", True),
        ("PENDING_PAYMENT", "NONE", True),
        ("CONFIRMED", "PENDING", True),
        ("CONFIRMED", "NONE", False),
        ("REFUSED", "NONE", False),
        ("EXPIRED", "NONE", False),
        ("CANCELLED", "NONE", False),
    ],
)
def test_poll_response_stops_for_terminal_states(
    monkeypatch, web_environment, status, refund_status, expected_polling
):
    def respond(request):
        if request.url.path == f"/registrations/{REGISTRATION_ID}":
            return httpx.Response(
                200,
                json=_registration(status, refund_status=refund_status),
            )
        raise AssertionError(f"Unexpected service request: {request.method} {request.url}")

    with _test_client(monkeypatch, web_environment, respond) as client:
        _select_participant(client)
        page = client.get(f"/registrations/{REGISTRATION_ID}")
        version = re.search(r'data-persona-version="(\d+)"', page.text)
        assert version is not None
        response = client.get(
            f"/registrations/{REGISTRATION_ID}/poll",
            params={"persona_version": version.group(1)},
        )

    assert response.status_code == 200
    assert response.json()["polling"] is expected_polling
    assert response.json()["registration"]["status"] == status


def test_persona_change_rejects_old_page_poll_without_backend_request(monkeypatch, web_environment):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json=_registration("PENDING_PAYMENT"))

    with _test_client(monkeypatch, web_environment, respond) as client:
        initial = client.get("/session").json()
        client.post(
            "/persona/participant",
            data={"csrf_token": initial["csrf_token"]},
            headers={"Origin": "http://testserver"},
            follow_redirects=False,
        )
        page = client.get(f"/registrations/{REGISTRATION_ID}")
        old_version = re.search(r'data-persona-version="(\d+)"', page.text)
        assert old_version is not None
        selected = client.get("/session").json()
        client.post(
            "/persona/participantTwo",
            data={"csrf_token": selected["csrf_token"]},
            headers={"Origin": "http://testserver"},
            follow_redirects=False,
        )
        response = client.get(
            f"/registrations/{REGISTRATION_ID}/poll",
            params={"persona_version": old_version.group(1)},
        )

    assert response.status_code == 409
    assert len(requests) == 1


def test_poll_failure_preserves_last_observed_registration_data(monkeypatch, web_environment):
    registration_reads = 0

    def respond(request):
        nonlocal registration_reads
        if request.url.path == f"/registrations/{REGISTRATION_ID}":
            registration_reads += 1
            if registration_reads == 2:
                return httpx.Response(503, json=_error())
            return httpx.Response(200, json=_registration("PENDING_PAYMENT"))
        raise AssertionError(f"Unexpected service request: {request.method} {request.url}")

    with _test_client(monkeypatch, web_environment, respond) as client:
        _select_participant(client)
        page = client.get(f"/registrations/{REGISTRATION_ID}")
        version = re.search(r'data-persona-version="(\d+)"', page.text)
        assert version is not None
        response = client.get(
            f"/registrations/{REGISTRATION_ID}/poll",
            params={"persona_version": version.group(1)},
        )

    assert response.status_code == 503
    assert response.json()["state"] == "unavailable"
    assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
    assert "registration" not in response.json()
    assert "PENDING_PAYMENT" in page.text
    assert "Last observed" in page.text


def test_lost_cancel_response_retries_same_idempotency_and_correlation(
    monkeypatch, web_environment
):
    mutation_requests = []

    def respond(request):
        if request.url.path == f"/registrations/{REGISTRATION_ID}":
            return httpx.Response(200, json=_registration("CONFIRMED"))
        if request.url.path == f"/registrations/{REGISTRATION_ID}/cancel":
            mutation_requests.append(request)
            if len(mutation_requests) == 1:
                raise httpx.ReadTimeout("result not observed")
            return httpx.Response(
                200,
                json=_registration("CANCELLED"),
            )
        raise AssertionError(f"Unexpected service request: {request.method} {request.url}")

    with _test_client(monkeypatch, web_environment, respond) as client:
        csrf_token = _select_participant(client)
        page = client.get(f"/registrations/{REGISTRATION_ID}")
        path = f"/registrations/{REGISTRATION_ID}/cancel"
        fields = _action_fields(page.text, path)
        first = client.post(
            path,
            data=fields,
            headers={"Origin": "http://testserver"},
            follow_redirects=False,
        )
        retry_fields = _action_fields(first.text, path)
        retry = client.post(
            path,
            data=retry_fields,
            headers={"Origin": "http://testserver"},
            follow_redirects=False,
        )

    assert csrf_token
    assert first.status_code == 503
    assert "result is unknown" in first.text.lower()
    assert retry.status_code == 303
    assert len(mutation_requests) == 2
    assert mutation_requests[0].headers["idempotency-key"] == fields["idempotency_key"]
    assert mutation_requests[1].headers["idempotency-key"] == fields["idempotency_key"]
    assert (
        mutation_requests[0].headers["x-correlation-id"]
        == mutation_requests[1].headers["x-correlation-id"]
    )
