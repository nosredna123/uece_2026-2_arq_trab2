import json
import re
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

import demo_web.app as app_module
from demo_web.app import create_app
from demo_web.clients import DemoClients


GESAD = "30000000-0000-4000-8000-000000000001"
UECE = "30000000-0000-4000-8000-000000000002"
CORRELATION_ID = "20000000-0000-4000-8000-000000000003"
ORIGIN = {"Origin": "http://testserver"}


class FakeBackend:
    """In-memory Event Management + Reporting good enough for the organizer pages."""

    def __init__(self):
        self.events = {}
        self.activities = {}
        self.reports = []
        self.requests = []
        self.reject = None

    def add_event(self, organization_id=GESAD, *, title="Semana GESAD", status="DRAFT"):
        event_id = str(uuid.uuid4())
        self.events[event_id] = {
            "id": event_id,
            "organizationId": organization_id,
            "title": title,
            "description": "Fixture",
            "startsAt": "2027-03-02T10:00:00Z",
            "endsAt": "2027-03-02T18:00:00Z",
            "status": status,
            "version": 1,
        }
        return event_id

    def add_activity(self, event_id, *, title="Palestra", capacity=10):
        activity_id = str(uuid.uuid4())
        self.activities[activity_id] = {
            "id": activity_id,
            "eventId": event_id,
            "organizationId": self.events[event_id]["organizationId"],
            "title": title,
            "startsAt": "2027-03-02T10:30:00Z",
            "endsAt": "2027-03-02T11:30:00Z",
            "location": "Auditório",
            "responsible": "Profa. Ana",
            "capacity": capacity,
            "price": {"amountMinor": 0, "currency": "BRL"},
            "version": 1,
            "capacityFrozen": False,
        }
        return activity_id

    def __call__(self, request):
        self.requests.append(request)
        path, method = request.url.path, request.method
        body = json.loads(request.read() or b"null")
        if self.reject and method != "GET":
            status, code = self.reject
            return httpx.Response(
                status,
                json={"code": code, "message": "Rejected by fixture.", "correlationId": CORRELATION_ID},
            )
        if match := re.fullmatch(r"/organizations/([^/]+)/events", path):
            return httpx.Response(
                200, json=[e for e in self.events.values() if e["organizationId"] == match[1]]
            )
        if match := re.fullmatch(r"/organizations/([^/]+)/events/([^/]+)/activities", path):
            return httpx.Response(
                200, json=[a for a in self.activities.values() if a["eventId"] == match[2]]
            )
        if match := re.fullmatch(r"/organizations/([^/]+)/reports", path):
            return httpx.Response(200, json=self.reports)
        if path == "/events" and method == "POST":
            event_id = self.add_event(body["organizationId"], title=body["title"])
            self.events[event_id].update(body)
            return httpx.Response(201, json=self.events[event_id])
        if match := re.fullmatch(r"/events/([^/]+)", path):
            self.events[match[1]].update(body, version=self.events[match[1]]["version"] + 1)
            return httpx.Response(200, json=self.events[match[1]])
        if match := re.fullmatch(r"/events/([^/]+)/activities", path):
            activity_id = self.add_activity(match[1])
            self.activities[activity_id].update(body)
            return httpx.Response(201, json=self.activities[activity_id])
        if match := re.fullmatch(r"/activities/([^/]+)", path):
            self.activities[match[1]].update(body)
            return httpx.Response(200, json=self.activities[match[1]])
        if match := re.fullmatch(r"/events/([^/]+)/(publish|cancel)", path):
            self.events[match[1]]["status"] = "PUBLISHED" if match[2] == "publish" else "CANCELLED"
            return httpx.Response(200, json=self.events[match[1]])
        raise AssertionError(f"Unexpected request: {method} {path}")

    def writes(self):
        return [r for r in self.requests if r.method != "GET"]


@pytest.fixture
def backend():
    return FakeBackend()


@pytest.fixture
def client(monkeypatch, web_environment, backend):
    monkeypatch.setattr(
        app_module,
        "DemoClients",
        lambda settings: DemoClients(settings, transport=httpx.MockTransport(backend)),
    )
    with TestClient(create_app(environ=web_environment)) as test_client:
        yield test_client


def _sign_in(client, persona="organizer"):
    csrf = client.get("/session").json()["csrf_token"]
    response = client.post(
        f"/persona/{persona}", data={"csrf_token": csrf}, headers=ORIGIN, follow_redirects=False
    )
    assert response.status_code == 303
    return response


def _form(html, action):
    match = re.search(rf'<form method="post" action="{re.escape(action)}"[^>]*>(.*?)</form>', html, re.S)
    assert match is not None, f"Missing form for {action}"
    return dict(re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)">', match[1]))


def _post(client, action, page_html, **fields):
    return client.post(
        action, data={**_form(page_html, action), **fields}, headers=ORIGIN, follow_redirects=False
    )


EVENT_FORM = {
    "title": "Semana de Engenharia de Software",
    "description": "Evento do GESAD",
    "starts_at": "2027-05-10T09:00",
    "ends_at": "2027-05-10T17:00",
}
ACTIVITY_FORM = {
    "title": "Minicurso de Arquitetura",
    "starts_at": "2027-03-02T13:00",
    "ends_at": "2027-03-02T15:00",
    "location": "Lab 3",
    "responsible": "Prof. Bruno",
    "capacity": "25",
    "price": "25,50",
}


@pytest.mark.parametrize(
    ("persona", "organization_id", "label"),
    [("organizer", GESAD, "GESAD/UECE"), ("organizerUece", UECE, "UECE")],
)
def test_my_events_lists_only_the_organization_events_including_drafts(
    client, backend, persona, organization_id, label
):
    backend.add_event(GESAD, title="Evento GESAD", status="PUBLISHED")
    backend.add_event(GESAD, title="Rascunho GESAD")
    backend.add_event(UECE, title="Evento UECE", status="CANCELLED")

    assert _sign_in(client, persona).headers["location"] == "/organizer"
    page = client.get("/organizer")

    assert page.status_code == 200
    assert label in page.text
    assert backend.requests[-1].url.path == f"/organizations/{organization_id}/events"
    if organization_id == GESAD:
        assert "Evento GESAD" in page.text and "Rascunho GESAD" in page.text
        assert "DRAFT" in page.text and "PUBLISHED" in page.text
        assert "Evento UECE" not in page.text
    else:
        assert "Evento UECE" in page.text and "CANCELLED" in page.text
        assert "GESAD" not in page.text.split("<main", 1)[1].replace("GESAD/UECE", "")


@pytest.mark.parametrize("path", ["/organizer", "/organizer/events/new", "/reports"])
def test_participants_are_sent_to_their_own_home_without_service_calls(client, backend, path):
    anonymous = client.get(path, follow_redirects=False)
    _sign_in(client, "participant")
    participant = client.get(path, follow_redirects=False)

    assert anonymous.headers["location"] == "/"
    assert participant.status_code == 303
    assert participant.headers["location"] == "/events"
    assert backend.requests == []


def test_visitor_is_not_a_sign_in_option(client):
    csrf = client.get("/session").json()["csrf_token"]
    response = client.post("/persona/visitor", data={"csrf_token": csrf}, headers=ORIGIN)
    assert response.status_code == 400


def test_creating_an_event_uses_the_organizer_scope_and_opens_its_page(client, backend):
    _sign_in(client)
    form_page = client.get("/organizer/events/new").text
    response = _post(
        client,
        "/organizer/events",
        form_page,
        **EVENT_FORM,
        organizationId="99999999-9999-4999-8999-999999999999",
    )
    assert response.status_code == 400  # unexpected fields are refused

    response = _post(client, "/organizer/events", form_page, **EVENT_FORM)
    replay = _post(client, "/organizer/events", form_page, **EVENT_FORM)

    assert response.status_code == 303
    assert replay.headers["location"] == response.headers["location"]
    assert response.headers["location"].endswith("?notice=created")
    (write,) = backend.writes()
    payload = json.loads(write.read())
    assert payload["organizationId"] == GESAD
    assert payload["startsAt"] == "2027-05-10T09:00:00Z"
    page = client.get(response.headers["location"])
    assert "Event created as a draft" in page.text
    assert "Publish event" in page.text


def test_invalid_schedule_is_rejected_before_the_service(client, backend):
    _sign_in(client)
    form_page = client.get("/organizer/events/new").text
    response = _post(
        client, "/organizer/events", form_page, **{**EVENT_FORM, "ends_at": "2027-05-10T08:00"}
    )

    assert response.status_code == 400
    assert "starts before it ends" in response.text
    assert 'value="Semana de Engenharia de Software"' in response.text
    assert backend.writes() == []


def test_event_page_edits_event_and_activities_then_publishes_and_cancels(client, backend):
    event_id = backend.add_event()
    activity_id = backend.add_activity(event_id)
    _sign_in(client)
    base = f"/organizer/events/{event_id}"

    page = client.get(base).text
    assert "Palestra" in page and "10 seats" in page
    saved = _post(client, base, page, **{**EVENT_FORM, "title": "Semana GESAD 2027"})
    assert saved.headers["location"] == f"{base}?notice=saved"

    page = client.get(base).text
    added = _post(client, f"{base}/activities", page, **ACTIVITY_FORM)
    assert added.headers["location"] == f"{base}?notice=activity-added"

    page = client.get(base).text
    edited = _post(
        client, f"{base}/activities/{activity_id}", page, **{**ACTIVITY_FORM, "capacity": "40"}
    )
    assert edited.headers["location"] == f"{base}?notice=activity-saved"

    page = client.get(base).text
    published = _post(client, f"{base}/publish", page)
    assert published.headers["location"] == f"{base}?notice=published"

    page = client.get(base).text
    assert "Publish event" not in page
    cancelled = _post(client, f"{base}/cancel", page)
    final = client.get(cancelled.headers["location"]).text

    writes = [(r.method, r.url.path) for r in backend.writes()]
    assert writes == [
        ("PUT", f"/events/{event_id}"),
        ("POST", f"/events/{event_id}/activities"),
        ("PUT", f"/activities/{activity_id}"),
        ("POST", f"/events/{event_id}/publish"),
        ("POST", f"/events/{event_id}/cancel"),
    ]
    new_activity = json.loads(backend.writes()[1].read())
    assert new_activity["price"] == {"amountMinor": 2550, "currency": "BRL"}
    assert backend.activities[activity_id]["capacity"] == 40
    assert backend.events[event_id]["title"] == "Semana GESAD 2027"
    assert "Event cancelled." in final
    assert "can no longer be edited" in final
    assert "Save event" not in final


def test_service_rejection_is_shown_on_the_form_and_keeps_the_same_key(client, backend):
    event_id = backend.add_event()
    _sign_in(client)
    base = f"/organizer/events/{event_id}"
    page = client.get(base).text
    key = _form(page, f"{base}/activities")["idempotency_key"]
    backend.reject = (409, "ACTIVITY_OUTSIDE_EVENT")

    response = _post(client, f"{base}/activities", page, **ACTIVITY_FORM)

    assert response.status_code == 409
    assert "Rejected by fixture. (ACTIVITY_OUTSIDE_EVENT)" in response.text
    assert _form(response.text, f"{base}/activities")["idempotency_key"] == key
    assert 'value="Minicurso de Arquitetura"' in response.text


def test_events_of_another_organization_are_not_found(client, backend):
    event_id = backend.add_event(UECE)
    _sign_in(client)

    response = client.get(f"/organizer/events/{event_id}", headers={"Accept": "text/html"})

    assert response.status_code == 404


def test_reports_join_published_events_activities_and_counters(client, backend):
    published = backend.add_event(title="Semana GESAD", status="PUBLISHED")
    with_report = backend.add_activity(published, title="Palestra", capacity=5)
    backend.add_activity(published, title="Oficina", capacity=8)
    backend.add_activity(backend.add_event(title="Rascunho"), title="Oculta")
    backend.reports = [
        {
            "organizationId": GESAD,
            "activityId": with_report,
            "confirmed": 3,
            "pendingPayment": 1,
            "offered": 1,
            "waiting": 2,
            "occupied": 4,
            "checkedIn": 2,
            "certificates": 1,
            "paymentsApproved": 0,
            "paymentsRefused": 0,
            "refundsApproved": 0,
            "projectedAt": "2027-03-02T10:15:00Z",
        }
    ]
    _sign_in(client)

    page = client.get("/reports")
    fragment = client.get("/reports/table")

    assert page.status_code == 200
    assert 'data-refresh-url="/reports/table"' in page.text
    assert "/static/reports.js" in page.text
    for html in (page.text, fragment.text):
        assert "Semana GESAD" in html and "Palestra" in html and "Oficina" in html
        assert "Rascunho" not in html and "Oculta" not in html
        cells = re.findall(r"<td>(\d+)</td>", html)
        assert cells[:6] == ["5", "3", "1", "3", "2", "1"]
        assert cells[6:12] == ["8", "0", "0", "0", "0", "0"]
        assert cells[12:] == ["13", "3", "1", "3", "2", "1"]
        assert _summary(html) == {
            "events": "1",
            "activities": "2",
            "capacity": "13",
            "confirmed": "3",
            "pending_payment": "1",
            "waiting": "3",
            "free_seats": "9",
            "checked_in": "2",
            "certificates": "1",
            "occupancy": "23%",
            "attendance": "67%",
        }
        trend_by_key = {key: trend for trend, key in re.findall(r'data-trend="([\w-]+)" data-summary="(\w+)"', html)}
        assert trend_by_key["confirmed"] == "up-good"
        assert trend_by_key["waiting"] == "down-good"
        assert trend_by_key["free_seats"] == "neutral"
        assert html.count("data-summary-delta") == 11
    assert "<html" not in fragment.text


def _summary(html: str) -> dict[str, str]:
    return dict(
        re.findall(
            r'data-summary="(\w+)">\s*<dt>[^<]*</dt>\s*<dd>([^<]*)</dd>',
            html,
        )
    )


def test_report_summary_without_published_events_shows_zeros(client, backend):
    backend.add_activity(backend.add_event(title="Rascunho"), title="Oculta", capacity=9)
    _sign_in(client)

    summary = _summary(client.get("/reports").text)

    assert summary["events"] == "0"
    assert summary["capacity"] == "0"
    assert summary["free_seats"] == "0"
    assert summary["occupancy"] == "—"
    assert summary["attendance"] == "—"
