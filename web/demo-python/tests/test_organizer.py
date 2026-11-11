import re

import httpx
import pytest
from fastapi.testclient import TestClient

import demo_web.app as app_module
from demo_web.app import create_app
from demo_web.clients import DemoClients


EVENT_ID = "20000000-0000-4000-8000-000000000001"
ACTIVITY_ID = "20000000-0000-4000-8000-000000000002"
ORGANIZATION_ID = "30000000-0000-4000-8000-000000000001"
CORRELATION_ID = "20000000-0000-4000-8000-000000000003"


def _event(*, status="DRAFT"):
    return {
        "id": EVENT_ID,
        "organizationId": ORGANIZATION_ID,
        "title": "Synthetic organizer event",
        "description": "Organizer test fixture",
        "startsAt": "2027-03-02T10:00:00Z",
        "endsAt": "2027-03-02T12:00:00Z",
        "status": status,
        "version": 1,
    }


def _activity():
    return {
        "id": ACTIVITY_ID,
        "eventId": EVENT_ID,
        "organizationId": ORGANIZATION_ID,
        "title": "Organizer workshop",
        "startsAt": "2027-03-02T10:30:00Z",
        "endsAt": "2027-03-02T11:30:00Z",
        "location": "Main hall",
        "responsible": "Synthetic organizer",
        "capacity": 12,
        "price": {"amountMinor": 2500, "currency": "BRL"},
        "version": 1,
        "capacityFrozen": False,
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


def _select_organizer(client, persona="organizer"):
    initial = client.get("/session").json()
    changed = client.post(
        f"/persona/{persona}",
        data={"csrf_token": initial["csrf_token"]},
        headers={"Origin": "http://testserver"},
        follow_redirects=False,
    )
    assert changed.status_code == 303
    return client.get("/session").json()["csrf_token"]


@pytest.mark.parametrize(
    ("persona", "label", "organization_id"),
    [
        ("organizer", "GESAD/UECE", "30000000-0000-4000-8000-000000000001"),
        ("organizerUece", "UECE", "30000000-0000-4000-8000-000000000002"),
        ("organizerAcademicSim", "IASC", "30000000-0000-4000-8000-000000000003"),
    ],
)
def test_organizer_workspace_displays_selected_tenant(
    monkeypatch, web_environment, persona, label, organization_id
):
    def unexpected_request(request):
        raise AssertionError(f"Organizer preview must not call services: {request.url}")

    with _test_client(monkeypatch, web_environment, unexpected_request) as client:
        _select_organizer(client, persona)
        response = client.get("/organizer")

    assert response.status_code == 200
    assert f"Organizador simulado — {label}" in response.text
    assert organization_id in response.text


def _form_fields(html, path):
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


def _create_event_data(csrf_token, idempotency_key, *, title="Organizer event"):
    return {
        "csrf_token": csrf_token,
        "idempotency_key": idempotency_key,
        "title": title,
        "description": "A session-scoped event draft.",
        "starts_at": "2027-03-02T10:00:00Z",
        "ends_at": "2027-03-02T12:00:00Z",
    }


def test_organizer_page_requires_organizer_persona_before_service_access(
    monkeypatch, web_environment, sign_in
):
    requests = []

    def respond(request):
        requests.append(request)
        raise AssertionError("Unauthorized organizer page must not call services.")

    with _test_client(monkeypatch, web_environment, respond) as client:
        sign_in(client)
        response = client.get("/organizer")

    assert response.status_code == 403
    assert requests == []


def test_event_creation_uses_fixture_organization_and_stays_unpublished(
    monkeypatch, web_environment
):
    requests = []

    def respond(request):
        requests.append(request)
        if request.url.path == "/events":
            assert request.method == "POST"
            payload = request.read()
            assert b'"organizationId":"' + ORGANIZATION_ID.encode() + b'"' in payload
            assert b'"organizationId":"99999999-9999-4999-8999-999999999999"' not in payload
            return httpx.Response(201, json=_event())
        raise AssertionError(f"Unexpected service request: {request.method} {request.url}")

    with _test_client(monkeypatch, web_environment, respond) as client:
        csrf_token = _select_organizer(client)
        page = client.get("/organizer")
        key = _form_fields(page.text, "/organizer/events")["idempotency_key"]
        response = client.post(
            "/organizer/events",
            data=_create_event_data(csrf_token, key),
            headers={"Origin": "http://testserver"},
            follow_redirects=False,
        )
        organizer_page = client.get("/organizer")

    assert page.status_code == 200
    assert response.status_code == 303
    assert response.headers["location"] == "/organizer"
    assert organizer_page.status_code == 200
    assert "Synthetic organizer event" in organizer_page.text
    assert "DRAFT" in organizer_page.text
    assert len(requests) == 1
    assert requests[0].headers["idempotency-key"] == key
    assert all("/publish" not in request.url.path for request in requests)


def test_invalid_event_schedule_is_rejected_without_service_mutation(monkeypatch, web_environment):
    requests = []

    def respond(request):
        requests.append(request)
        raise AssertionError("Invalid event schedule must be rejected before the API.")

    with _test_client(monkeypatch, web_environment, respond) as client:
        csrf_token = _select_organizer(client)
        page = client.get("/organizer")
        key = _form_fields(page.text, "/organizer/events")["idempotency_key"]
        response = client.post(
            "/organizer/events",
            data={
                **_create_event_data(csrf_token, key),
                "starts_at": "2027-03-02T12:00:00Z",
                "ends_at": "2027-03-02T10:00:00Z",
            },
            headers={"Origin": "http://testserver"},
            follow_redirects=False,
        )

    assert response.status_code == 400
    assert "schedule" in response.text.lower() or "event" in response.text.lower()
    assert requests == []


def test_session_draft_is_not_visible_to_another_browser_session(monkeypatch, web_environment):
    def respond(request):
        if request.url.path == "/events":
            return httpx.Response(201, json=_event())
        raise AssertionError(f"Unexpected service request: {request.method} {request.url}")

    with _test_client(monkeypatch, web_environment, respond) as client:
        csrf_token = _select_organizer(client)
        page = client.get("/organizer")
        key = _form_fields(page.text, "/organizer/events")["idempotency_key"]
        created = client.post(
            "/organizer/events",
            data=_create_event_data(csrf_token, key),
            headers={"Origin": "http://testserver"},
            follow_redirects=False,
        )
        first_session_page = client.get("/organizer")
        first_cookie = client.cookies.get("demo_session")
        client.cookies.clear()
        second_csrf = _select_organizer(client)
        second_session_page = client.get("/organizer")
        foreign_draft = client.post(
            f"/organizer/events/{EVENT_ID}/publish",
            data={"csrf_token": second_csrf, "idempotency_key": "foreign-draft-command"},
            headers={"Origin": "http://testserver"},
            follow_redirects=False,
        )

    assert created.status_code == 303
    assert first_cookie
    assert "Synthetic organizer event" in first_session_page.text
    assert "Synthetic organizer event" not in second_session_page.text
    assert foreign_draft.status_code == 404
    assert second_csrf


def test_duplicate_event_creation_reuses_one_command(monkeypatch, web_environment):
    requests = []

    def respond(request):
        if request.url.path == "/events":
            requests.append(request)
            return httpx.Response(201, json=_event())
        raise AssertionError(f"Unexpected service request: {request.method} {request.url}")

    with _test_client(monkeypatch, web_environment, respond) as client:
        csrf_token = _select_organizer(client)
        page = client.get("/organizer")
        key = _form_fields(page.text, "/organizer/events")["idempotency_key"]
        payload = _create_event_data(csrf_token, key)
        first = client.post(
            "/organizer/events",
            data=payload,
            headers={"Origin": "http://testserver"},
            follow_redirects=False,
        )
        duplicate = client.post(
            "/organizer/events",
            data=payload,
            headers={"Origin": "http://testserver"},
            follow_redirects=False,
        )
        organizer_page = client.get("/organizer")

    assert first.status_code == duplicate.status_code == 303
    assert len(requests) == 1
    assert "Synthetic organizer event" in organizer_page.text
    assert organizer_page.text.count(f"Event {EVENT_ID}") == 1


def test_lost_event_creation_response_retries_same_session_step(monkeypatch, web_environment):
    requests = []

    def respond(request):
        if request.url.path == "/events":
            requests.append(request)
            if len(requests) == 1:
                raise httpx.ReadTimeout("result not observed")
            return httpx.Response(201, json=_event())
        raise AssertionError(f"Unexpected service request: {request.method} {request.url}")

    with _test_client(monkeypatch, web_environment, respond) as client:
        csrf_token = _select_organizer(client)
        page = client.get("/organizer")
        key = _form_fields(page.text, "/organizer/events")["idempotency_key"]
        first = client.post(
            "/organizer/events",
            data=_create_event_data(csrf_token, key),
            headers={"Origin": "http://testserver"},
            follow_redirects=False,
        )
        retry_fields = _form_fields(first.text, "/organizer/events")
        retry = client.post(
            "/organizer/events",
            data={
                **retry_fields,
                **_create_event_data(retry_fields["csrf_token"], retry_fields["idempotency_key"]),
            },
            headers={"Origin": "http://testserver"},
            follow_redirects=False,
        )

    assert first.status_code == 503
    assert "unknown" in first.text.lower()
    assert retry.status_code == 303
    assert len(requests) == 2
    assert requests[0].headers["idempotency-key"] == requests[1].headers["idempotency-key"] == key
    assert requests[0].headers["x-correlation-id"] == requests[1].headers["x-correlation-id"]


def test_lost_activity_creation_response_retries_same_session_step(monkeypatch, web_environment):
    activity_requests = []

    def respond(request):
        if request.url.path == "/events":
            return httpx.Response(201, json=_event())
        if request.url.path == f"/events/{EVENT_ID}/activities":
            activity_requests.append(request)
            if len(activity_requests) == 1:
                raise httpx.ReadTimeout("result not observed")
            return httpx.Response(201, json=_activity())
        raise AssertionError(f"Unexpected service request: {request.method} {request.url}")

    with _test_client(monkeypatch, web_environment, respond) as client:
        csrf_token = _select_organizer(client)
        page = client.get("/organizer")
        create_key = _form_fields(page.text, "/organizer/events")["idempotency_key"]
        client.post(
            "/organizer/events",
            data=_create_event_data(csrf_token, create_key),
            headers={"Origin": "http://testserver"},
            follow_redirects=False,
        )
        page = client.get("/organizer")
        action = f"/organizer/events/{EVENT_ID}/activities"
        activity_fields = _form_fields(page.text, action)
        activity_data = {
            **activity_fields,
            "title": "Organizer workshop",
            "starts_at": "2027-03-02T10:30:00Z",
            "ends_at": "2027-03-02T11:30:00Z",
            "location": "Main hall",
            "responsible": "Synthetic organizer",
            "capacity": "12",
            "amount_minor": "2500",
            "currency": "BRL",
        }
        first = client.post(
            action,
            data=activity_data,
            headers={"Origin": "http://testserver"},
            follow_redirects=False,
        )
        retry_fields = _form_fields(first.text, action)
        retry = client.post(
            action,
            data={**activity_data, **retry_fields},
            headers={"Origin": "http://testserver"},
            follow_redirects=False,
        )
        organizer_page = client.get("/organizer")

    assert first.status_code == 503
    assert retry.status_code == 303
    assert len(activity_requests) == 2
    assert (
        activity_requests[0].headers["idempotency-key"]
        == activity_requests[1].headers["idempotency-key"]
        == activity_fields["idempotency_key"]
    )
    assert (
        activity_requests[0].headers["x-correlation-id"]
        == activity_requests[1].headers["x-correlation-id"]
    )
    assert "Organizer workshop" in organizer_page.text


def test_activity_and_publish_are_scoped_to_session_draft_and_explicit(
    monkeypatch, web_environment
):
    requests = []

    def respond(request):
        requests.append(request)
        if request.url.path == "/events":
            return httpx.Response(201, json=_event())
        if request.url.path == f"/events/{EVENT_ID}/activities":
            assert request.method == "POST"
            assert b'"organizationId"' not in request.read()
            return httpx.Response(201, json=_activity())
        if request.url.path == f"/events/{EVENT_ID}/publish":
            assert request.method == "POST"
            return httpx.Response(200, json=_event(status="PUBLISHED"))
        raise AssertionError(f"Unexpected service request: {request.method} {request.url}")

    with _test_client(monkeypatch, web_environment, respond) as client:
        csrf_token = _select_organizer(client)
        page = client.get("/organizer")
        create_key = _form_fields(page.text, "/organizer/events")["idempotency_key"]
        created = client.post(
            "/organizer/events",
            data=_create_event_data(csrf_token, create_key),
            headers={"Origin": "http://testserver"},
            follow_redirects=False,
        )
        page = client.get("/organizer")
        activity_path = f"/organizer/events/{EVENT_ID}/activities"
        activity_fields = _form_fields(page.text, activity_path)
        activity_response = client.post(
            activity_path,
            data={
                **activity_fields,
                "title": "Organizer workshop",
                "starts_at": "2027-03-02T10:30:00Z",
                "ends_at": "2027-03-02T11:30:00Z",
                "location": "Main hall",
                "responsible": "Synthetic organizer",
                "capacity": "12",
                "amount_minor": "2500",
                "currency": "BRL",
            },
            headers={"Origin": "http://testserver"},
            follow_redirects=False,
        )
        page = client.get("/organizer")
        publish_path = f"/organizer/events/{EVENT_ID}/publish"
        publish_fields = _form_fields(page.text, publish_path)
        publish_response = client.post(
            publish_path,
            data=publish_fields,
            headers={"Origin": "http://testserver"},
            follow_redirects=False,
        )
        publish_replay = client.post(
            publish_path,
            data=publish_fields,
            headers={"Origin": "http://testserver"},
            follow_redirects=False,
        )
        completed_page = client.get("/organizer")

    assert created.status_code == 303
    assert activity_response.status_code == 303
    assert publish_response.status_code == 303
    assert publish_replay.status_code == 303
    assert len(requests) == 3
    assert "Organizer workshop" in completed_page.text
    assert "PUBLISHED" in completed_page.text
    assert requests[1].url.path == f"/events/{EVENT_ID}/activities"
    assert requests[2].url.path == f"/events/{EVENT_ID}/publish"


def test_cross_organization_fields_are_rejected(monkeypatch, web_environment):
    requests = []

    def respond(request):
        requests.append(request)
        raise AssertionError("Cross-organization form data must not mutate the service.")

    with _test_client(monkeypatch, web_environment, respond) as client:
        csrf_token = _select_organizer(client)
        page = client.get("/organizer")
        key = _form_fields(page.text, "/organizer/events")["idempotency_key"]
        response = client.post(
            "/organizer/events",
            data={
                **_create_event_data(csrf_token, key),
                "organizationId": "99999999-9999-4999-8999-999999999999",
            },
            headers={"Origin": "http://testserver"},
            follow_redirects=False,
        )

    assert response.status_code == 400
    assert requests == []
