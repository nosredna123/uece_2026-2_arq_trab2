import json
import re
from datetime import datetime, timedelta, timezone
from uuid import UUID

import httpx
from fastapi.testclient import TestClient

import demo_web.app as app_module
import demo_web.routes as routes_module
from demo_web.app import create_app
from demo_web.clients import DemoClients


ORGANIZATION_ID = "30000000-0000-4000-8000-000000000001"


def _event_reply(payload, event_id, *, status="DRAFT"):
    return {
        **payload,
        "id": event_id,
        "status": status,
        "version": 1,
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


def _select_organizer(client):
    initial = client.get("/session").json()
    changed = client.post(
        "/persona/organizer",
        data={"csrf_token": initial["csrf_token"]},
        headers={"Origin": "http://testserver"},
        follow_redirects=False,
    )
    assert changed.status_code == 303
    return client.get("/session").json()["csrf_token"]


def _hidden_fields(html, path="/demo/prepare"):
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


def _resume_fields(html):
    forms = re.findall(
        r'<form[^>]+action="/demo/prepare"[^>]*>(.*?)</form>',
        html,
        re.DOTALL,
    )
    form = next(
        (content for content in forms if 'name="intent" value="resume"' in content),
        None,
    )
    assert form is not None, "Missing resume form"
    return {
        name: value
        for name, value in re.findall(
            r'<input type="hidden" name="([^"]+)" value="([^"]*)">',
            form,
        )
    }


def _new_run_fields(client, *, confirm=True):
    page = client.get("/demo")
    fields = _hidden_fields(page.text)
    fields["intent"] = "new"
    if confirm:
        fields["confirm_new_run"] = "yes"
    return fields


def _run_id(html):
    match = re.search(r'data-run-id="([0-9a-f-]{36})"', html)
    assert match is not None, "Missing stable preparation run ID"
    return UUID(match.group(1))


class _Service:
    def __init__(self):
        self.requests = []
        self.next_id = 1
        self.events = {}

    def __call__(self, request):
        self.requests.append(request)
        payload = json.loads(request.content) if request.content else {}
        if request.method == "POST" and request.url.path == "/events":
            event_id = self._id()
            event = _event_reply(payload, event_id)
            self.events[event_id] = event
            return httpx.Response(201, json=event)
        match = re.fullmatch(r"/events/([0-9a-f-]+)/activities", request.url.path)
        if request.method == "POST" and match:
            activity_id = self._id()
            activity = {
                **payload,
                "id": activity_id,
                "eventId": match.group(1),
                "organizationId": ORGANIZATION_ID,
                "version": 1,
                "capacityFrozen": False,
            }
            return httpx.Response(201, json=activity)
        match = re.fullmatch(r"/events/([0-9a-f-]+)/publish", request.url.path)
        if request.method == "POST" and match:
            event = _event_reply(
                self.events[match.group(1)],
                match.group(1),
                status="PUBLISHED",
            )
            self.events[match.group(1)] = event
            return httpx.Response(200, json=event)
        raise AssertionError(f"Unexpected service request: {request.method} {request.url}")

    def _id(self):
        value = f"50000000-0000-4000-8000-{self.next_id:012d}"
        self.next_id += 1
        return value


def _fixed_clock(monkeypatch, initial):
    current = [initial]

    class FrozenDateTime:
        @classmethod
        def now(cls, tz=None):
            return current[0].astimezone(tz) if tz else current[0].replace(tzinfo=None)

    monkeypatch.setattr(routes_module, "datetime", FrozenDateTime)
    return current


def test_demo_preview_is_read_only_and_requires_explicit_fresh_run(monkeypatch, web_environment):
    service = _Service()
    with _test_client(monkeypatch, web_environment, service) as client:
        csrf_token = _select_organizer(client)
        preview = client.get("/demo")
        script = client.get("/static/demo.js")
        unconfirmed = _new_run_fields(client, confirm=False)
        unconfirmed["csrf_token"] = csrf_token
        rejected = client.post(
            "/demo/prepare",
            data=unconfirmed,
            headers={"Origin": "http://testserver"},
            follow_redirects=False,
        )

    assert preview.status_code == 200
    assert script.status_code == 200
    assert "scheduled window has closed" in script.text
    assert "Prepare a new run" in preview.text
    assert 'name="confirm_new_run"' in preview.text
    assert "No registration has been created by this countdown" in preview.text
    assert rejected.status_code == 400
    assert service.requests == []


def test_preparation_uses_unique_valid_paid_and_one_seat_free_schedules(
    monkeypatch, web_environment
):
    now = datetime(2027, 3, 2, 10, 0, tzinfo=timezone.utc)
    _fixed_clock(monkeypatch, now)
    service = _Service()
    with _test_client(monkeypatch, web_environment, service) as client:
        csrf_token = _select_organizer(client)
        fields = _new_run_fields(client)
        fields["csrf_token"] = csrf_token
        response = client.post(
            "/demo/prepare",
            data=fields,
            headers={"Origin": "http://testserver"},
            follow_redirects=True,
        )

    assert response.status_code == 200
    run_id = _run_id(response.text)
    assert str(run_id) in response.text
    creates = [request for request in service.requests if request.method == "POST"]
    event_request = next(request for request in creates if request.url.path == "/events")
    event = json.loads(event_request.content)
    activity_requests = [request for request in creates if request.url.path.endswith("/activities")]
    activities = [json.loads(request.content) for request in activity_requests]
    paid = next(item for item in activities if item["price"]["amountMinor"] > 0)
    free = next(item for item in activities if item["price"]["amountMinor"] == 0)
    published = next(request for request in creates if request.url.path.endswith("/publish"))

    assert event["organizationId"] == ORGANIZATION_ID
    assert str(run_id) in event["title"]
    assert str(run_id) in paid["title"] and str(run_id) in free["title"]
    assert len(activities) == 2
    assert paid["capacity"] > 0
    assert free["capacity"] == 1
    assert paid["price"]["currency"] == free["price"]["currency"] == "BRL"
    event_start = datetime.fromisoformat(event["startsAt"])
    event_end = datetime.fromisoformat(event["endsAt"])
    paid_start = datetime.fromisoformat(paid["startsAt"])
    paid_end = datetime.fromisoformat(paid["endsAt"])
    free_start = datetime.fromisoformat(free["startsAt"])
    free_end = datetime.fromisoformat(free["endsAt"])
    assert all(
        value.tzinfo is not None
        for value in (event_start, event_end, paid_start, paid_end, free_start, free_end)
    )
    assert event_start < paid_start < paid_end < free_start < free_end < event_end
    assert (paid_start - now).total_seconds() == 180
    assert (paid_end - paid_start).total_seconds() == 60
    assert (free_start - now).total_seconds() == 900
    assert "PUBLISHED" in response.text
    assert "Registration status is not determined by countdowns" in response.text
    assert published.headers["idempotency-key"]
    assert all(request.method != "DELETE" for request in service.requests)


def test_repeated_submit_resumes_same_run_and_fresh_intent_never_resets_data(
    monkeypatch, web_environment
):
    service = _Service()
    with _test_client(monkeypatch, web_environment, service) as client:
        csrf_token = _select_organizer(client)
        first_fields = _new_run_fields(client)
        first_fields["csrf_token"] = csrf_token
        first = client.post(
            "/demo/prepare",
            data=first_fields,
            headers={"Origin": "http://testserver"},
            follow_redirects=True,
        )
        duplicate = client.post(
            "/demo/prepare",
            data=first_fields,
            headers={"Origin": "http://testserver"},
            follow_redirects=True,
        )
        second_fields = _new_run_fields(client)
        second_fields["csrf_token"] = csrf_token
        second = client.post(
            "/demo/prepare",
            data=second_fields,
            headers={"Origin": "http://testserver"},
            follow_redirects=True,
        )

    first_run = _run_id(first.text)
    assert _run_id(duplicate.text) == first_run
    assert _run_id(second.text) != first_run
    assert len([r for r in service.requests if r.url.path == "/events"]) == 2
    assert len([r for r in service.requests if r.url.path.endswith("/activities")]) == 4
    assert len([r for r in service.requests if r.url.path.endswith("/publish")]) == 2
    assert all(request.method != "DELETE" for request in service.requests)


def test_partial_unknown_failure_retries_same_step_key_and_run(monkeypatch, web_environment):
    service = _Service()
    failed_once = False

    def respond(request):
        nonlocal failed_once
        if request.url.path.endswith("/activities") and not failed_once:
            payload = json.loads(request.content)
            if payload["price"]["amountMinor"] == 0:
                service.requests.append(request)
                failed_once = True
                raise httpx.ReadTimeout("test timeout after sending")
        return service(request)

    with _test_client(monkeypatch, web_environment, respond) as client:
        csrf_token = _select_organizer(client)
        fields = _new_run_fields(client)
        fields["csrf_token"] = csrf_token
        failed = client.post(
            "/demo/prepare",
            data=fields,
            headers={"Origin": "http://testserver"},
            follow_redirects=False,
        )
        failed_run = _run_id(failed.text)
        resume_fields = _resume_fields(failed.text)
        retry = client.post(
            "/demo/prepare",
            data=resume_fields,
            headers={"Origin": "http://testserver"},
            follow_redirects=True,
        )

    assert failed.status_code == 503
    assert _run_id(retry.text) == failed_run
    event_requests = [r for r in service.requests if r.url.path == "/events"]
    paid_requests = [
        r
        for r in service.requests
        if r.url.path.endswith("/activities") and json.loads(r.content)["price"]["amountMinor"] > 0
    ]
    free_requests = [
        r
        for r in service.requests
        if r.url.path.endswith("/activities") and json.loads(r.content)["price"]["amountMinor"] == 0
    ]
    publish_requests = [r for r in service.requests if r.url.path.endswith("/publish")]
    assert len(event_requests) == len(paid_requests) == len(publish_requests) == 1
    assert len(free_requests) == 2
    assert len({r.headers["idempotency-key"] for r in free_requests}) == 1
    assert len({r.headers["x-correlation-id"] for r in free_requests}) == 1
    assert "Resume preparation" in failed.text


def test_stale_paid_schedule_warns_without_claiming_registration_success(
    monkeypatch, web_environment
):
    now = datetime(2027, 3, 2, 10, 0, tzinfo=timezone.utc)
    current = _fixed_clock(monkeypatch, now)
    service = _Service()
    with _test_client(monkeypatch, web_environment, service) as client:
        csrf_token = _select_organizer(client)
        fields = _new_run_fields(client)
        fields["csrf_token"] = csrf_token
        prepared = client.post(
            "/demo/prepare",
            data=fields,
            headers={"Origin": "http://testserver"},
            follow_redirects=True,
        )
        run_id = _run_id(prepared.text)
        current[0] = now + timedelta(minutes=4)
        stale = client.get(f"/demo?run_id={run_id}")

    assert stale.status_code == 200
    assert "Paid activity window has closed" in stale.text
    assert "Registration status is not determined by countdowns" in stale.text
    assert len(service.requests) == 4
