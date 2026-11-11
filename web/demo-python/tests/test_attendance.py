import re

import httpx
import pytest
from fastapi.testclient import TestClient

import demo_web.app as app_module
from demo_web.app import create_app
from demo_web.clients import DemoClients


REGISTRATION_ID = "40000000-0000-4000-8000-000000000001"
ACTIVITY_ID = "40000000-0000-4000-8000-000000000002"
PARTICIPANT_ID = "40000000-0000-4000-8000-000000000003"
ORGANIZATION_ID = "40000000-0000-4000-8000-000000000004"
CERTIFICATE_ID = "40000000-0000-4000-8000-000000000005"
CORRELATION_ID = "40000000-0000-4000-8000-000000000006"


def _registration(status="CONFIRMED"):
    return {
        "id": REGISTRATION_ID,
        "activityId": ACTIVITY_ID,
        "participantId": PARTICIPANT_ID,
        "organizationId": ORGANIZATION_ID,
        "status": status,
        "refundStatus": "NONE",
        "createdAt": "2027-03-02T10:00:00Z",
        "version": 1,
        "expiresAt": None,
        "offerExpiresAt": None,
        "waitlistSequence": None,
        "price": {"amountMinor": 0, "currency": "BRL"},
    }


def _check_in():
    return {
        "registrationId": REGISTRATION_ID,
        "participantId": PARTICIPANT_ID,
        "checkedInAt": "2027-03-02T11:00:00Z",
    }


def _certificate():
    return {
        "id": CERTIFICATE_ID,
        "registrationId": REGISTRATION_ID,
        "participantId": PARTICIPANT_ID,
        "activityId": ACTIVITY_ID,
        "issuedAt": "2027-03-02T12:05:00Z",
    }


def _activity(title="Workshop"):
    return {
        "id": ACTIVITY_ID,
        "eventId": "40000000-0000-4000-8000-000000000007",
        "organizationId": ORGANIZATION_ID,
        "title": title,
        "startsAt": "2027-03-02T10:30:00Z",
        "endsAt": "2027-03-02T11:30:00Z",
        "location": "Main hall",
        "responsible": "Synthetic organizer",
        "capacity": 4,
        "price": {"amountMinor": 0, "currency": "BRL"},
        "version": 1,
        "capacityFrozen": False,
    }


def _error(code):
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
    return {
        name: value
        for name, value in re.findall(
            r'<input type="hidden" name="([^"]+)" value="([^"]*)">',
            form.group(1),
        )
    }


def test_check_in_posts_once_and_duplicate_replays_backend_record(monkeypatch, web_environment):
    check_in_requests = []

    def respond(request):
        if request.url.path == f"/registrations/{REGISTRATION_ID}":
            return httpx.Response(200, json=_registration())
        if request.url.path == f"/registrations/{REGISTRATION_ID}/check-in":
            check_in_requests.append(request)
            return httpx.Response(200, json=_check_in())
        raise AssertionError(f"Unexpected service request: {request.method} {request.url}")

    with _test_client(monkeypatch, web_environment, respond) as client:
        _select_participant(client)
        page = client.get(f"/registrations/{REGISTRATION_ID}")
        path = f"/registrations/{REGISTRATION_ID}/check-in"
        fields = _action_fields(page.text, path)
        first = client.post(
            path,
            data=fields,
            headers={"Origin": "http://testserver"},
            follow_redirects=False,
        )
        replay = client.post(
            path,
            data=fields,
            headers={"Origin": "http://testserver"},
            follow_redirects=False,
        )

    assert first.status_code == replay.status_code == 303
    assert (
        first.headers["location"]
        == replay.headers["location"]
        == f"/registrations/{REGISTRATION_ID}"
    )
    assert len(check_in_requests) == 1
    assert check_in_requests[0].headers["idempotency-key"] == fields["idempotency_key"]


def test_check_in_read_state_is_unknown_after_one_time_post_observation(
    monkeypatch, web_environment
):
    requests = []

    def respond(request):
        requests.append(request)
        if request.url.path == f"/registrations/{REGISTRATION_ID}":
            return httpx.Response(200, json=_registration())
        if request.url.path == f"/registrations/{REGISTRATION_ID}/check-in":
            return httpx.Response(200, json=_check_in())
        raise AssertionError(f"Unexpected service request: {request.method} {request.url}")

    with _test_client(monkeypatch, web_environment, respond) as client:
        _select_participant(client)
        page = client.get(f"/registrations/{REGISTRATION_ID}")
        path = f"/registrations/{REGISTRATION_ID}/check-in"
        fields = _action_fields(page.text, path)
        post_redirect = client.post(
            path,
            data=fields,
            headers={"Origin": "http://testserver"},
            follow_redirects=True,
        )
        reloaded = client.get(f"/registrations/{REGISTRATION_ID}")

    assert post_redirect.status_code == 200
    assert "Checked in at" in post_redirect.text
    assert "Checked in at" not in reloaded.text
    assert all(
        request.url.path != f"/registrations/{REGISTRATION_ID}/check-in" or request.method == "POST"
        for request in requests
    )


def test_persona_change_clears_check_in_observation(monkeypatch, web_environment):
    def respond(request):
        if request.url.path == f"/registrations/{REGISTRATION_ID}":
            return httpx.Response(200, json=_registration())
        if request.url.path == f"/registrations/{REGISTRATION_ID}/check-in":
            return httpx.Response(200, json=_check_in())
        raise AssertionError(f"Unexpected service request: {request.method} {request.url}")

    with _test_client(monkeypatch, web_environment, respond) as client:
        _select_participant(client)
        page = client.get(f"/registrations/{REGISTRATION_ID}")
        path = f"/registrations/{REGISTRATION_ID}/check-in"
        client.post(
            path,
            data=_action_fields(page.text, path),
            headers={"Origin": "http://testserver"},
            follow_redirects=False,
        )
        current_token = client.get("/session").json()["csrf_token"]
        changed = client.post(
            "/persona/participantTwo",
            data={"csrf_token": current_token},
            headers={"Origin": "http://testserver"},
            follow_redirects=False,
        )
        reloaded = client.get(f"/registrations/{REGISTRATION_ID}")

    assert changed.status_code == 303
    assert "Checked in at" not in reloaded.text
    assert "Check in" in reloaded.text


@pytest.mark.parametrize(
    ("status_code", "code"),
    [
        (409, "ELIGIBILITY_NOT_READY"),
        (422, "REGISTRATION_NOT_CONFIRMED"),
    ],
)
def test_check_in_preserves_projection_and_eligibility_failures(
    monkeypatch, web_environment, status_code, code
):
    def respond(request):
        if request.url.path == f"/registrations/{REGISTRATION_ID}":
            return httpx.Response(200, json=_registration())
        if request.url.path == f"/registrations/{REGISTRATION_ID}/check-in":
            return httpx.Response(status_code, json=_error(code))
        raise AssertionError(f"Unexpected service request: {request.method} {request.url}")

    with _test_client(monkeypatch, web_environment, respond) as client:
        _select_participant(client)
        page = client.get(f"/registrations/{REGISTRATION_ID}")
        path = f"/registrations/{REGISTRATION_ID}/check-in"
        fields = _action_fields(page.text, path)
        response = client.post(
            path,
            data=fields,
            headers={"Origin": "http://testserver"},
            follow_redirects=False,
        )

    assert response.status_code == status_code
    assert code in response.text
    assert "Check-in recorded" not in response.text


def test_certificate_issuance_before_activity_end_shows_backend_rejection(
    monkeypatch, web_environment
):
    certificate_requests = []

    def respond(request):
        if request.url.path == f"/registrations/{REGISTRATION_ID}":
            return httpx.Response(200, json=_registration())
        if request.url.path == f"/registrations/{REGISTRATION_ID}/certificate":
            certificate_requests.append(request)
            return httpx.Response(422, json=_error("ACTIVITY_NOT_ENDED"))
        raise AssertionError(f"Unexpected service request: {request.method} {request.url}")

    with _test_client(monkeypatch, web_environment, respond) as client:
        _select_participant(client)
        page = client.get(f"/registrations/{REGISTRATION_ID}")
        path = f"/registrations/{REGISTRATION_ID}/certificate"
        fields = _action_fields(page.text, path)
        response = client.post(
            path,
            data=fields,
            headers={"Origin": "http://testserver"},
            follow_redirects=False,
        )

    assert response.status_code == 422
    assert "ACTIVITY_NOT_ENDED" in response.text
    assert "Certificate issued" not in response.text
    assert len(certificate_requests) == 1
    assert certificate_requests[0].method == "POST"


def test_foreign_or_missing_certificate_remains_not_found(monkeypatch, web_environment):
    requests = []

    def respond(request):
        requests.append(request)
        if request.url.path == f"/registrations/{REGISTRATION_ID}/certificate":
            return httpx.Response(404, json=_error("CERTIFICATE_NOT_FOUND"))
        raise AssertionError(f"Unexpected service request: {request.method} {request.url}")

    with _test_client(monkeypatch, web_environment, respond) as client:
        _select_participant(client)
        response = client.get(f"/registrations/{REGISTRATION_ID}/certificate")

    assert response.status_code == 404
    assert "not found" in response.text.lower()
    assert CERTIFICATE_ID not in response.text
    assert len(requests) == 1
    assert requests[0].method == "GET"


def test_issue_then_retrieve_certificate_uses_attendance_record_identity(
    monkeypatch, web_environment
):
    requests = []

    def respond(request):
        requests.append(request)
        if request.url.path == f"/registrations/{REGISTRATION_ID}":
            return httpx.Response(200, json=_registration())
        if request.url.path == f"/registrations/{REGISTRATION_ID}/certificate":
            if request.method == "POST":
                return httpx.Response(200, json=_certificate())
            return httpx.Response(200, json=_certificate())
        if request.url.path == f"/activities/{ACTIVITY_ID}":
            return httpx.Response(200, json=_activity())
        raise AssertionError(f"Unexpected service request: {request.method} {request.url}")

    with _test_client(monkeypatch, web_environment, respond) as client:
        _select_participant(client)
        page = client.get(f"/registrations/{REGISTRATION_ID}")
        path = f"/registrations/{REGISTRATION_ID}/certificate"
        fields = _action_fields(page.text, path)
        response = client.post(
            path,
            data=fields,
            headers={"Origin": "http://testserver"},
            follow_redirects=True,
        )

    assert response.status_code == 200
    assert "Certificate of participation" in response.text
    assert CERTIFICATE_ID in response.text
    assert REGISTRATION_ID in response.text
    assert PARTICIPANT_ID in response.text
    assert ACTIVITY_ID in response.text
    assert "Mar 02, 2027 12:05 UTC" in response.text
    assert [
        request.method for request in requests if request.url.path.endswith("/certificate")
    ] == [
        "POST",
        "GET",
    ]


def test_printable_certificate_escapes_activity_content_and_never_mutates(
    monkeypatch, web_environment
):
    requests = []

    def respond(request):
        requests.append(request)
        if request.url.path == f"/registrations/{REGISTRATION_ID}/certificate":
            return httpx.Response(200, json=_certificate())
        if request.url.path == f"/activities/{ACTIVITY_ID}":
            return httpx.Response(200, json=_activity('<script>alert("unsafe")</script>'))
        raise AssertionError(f"Unexpected service request: {request.method} {request.url}")

    with _test_client(monkeypatch, web_environment, respond) as client:
        _select_participant(client)
        response = client.get(f"/registrations/{REGISTRATION_ID}/certificate")
        print_styles = client.get("/static/print.css")

    assert response.status_code == 200
    assert "<script>" not in response.text
    assert "&lt;script&gt;" in response.text
    assert "not a signed credential" in response.text
    assert "browser Print command" in response.text
    assert print_styles.status_code == 200
    assert "@media print" in print_styles.text
    assert all(request.method == "GET" for request in requests)
