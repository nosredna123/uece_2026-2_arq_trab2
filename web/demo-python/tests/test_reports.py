import re

import httpx
from fastapi.testclient import TestClient

import demo_web.app as app_module
from demo_web.app import create_app
from demo_web.clients import DemoClients


ORGANIZATION_ID = "30000000-0000-4000-8000-000000000001"
ACTIVITY_ID = "60000000-0000-4000-8000-000000000001"
OTHER_ACTIVITY_ID = "60000000-0000-4000-8000-000000000002"
CORRELATION_ID = "60000000-0000-4000-8000-000000000003"


def _report(
    *,
    activity_id=ACTIVITY_ID,
    confirmed=2,
    pending=1,
    offered=1,
    waiting=3,
    occupied=4,
    checked_in=1,
    certificates=1,
):
    return {
        "organizationId": ORGANIZATION_ID,
        "activityId": activity_id,
        "confirmed": confirmed,
        "pendingPayment": pending,
        "offered": offered,
        "waiting": waiting,
        "occupied": occupied,
        "checkedIn": checked_in,
        "certificates": certificates,
        "paymentsApproved": 2,
        "paymentsRefused": 1,
        "refundsApproved": 0,
        "projectedAt": "2027-03-02T10:15:00Z",
    }


def _failure():
    return {
        "code": "REPORTING_UNAVAILABLE",
        "message": "A sanitized reporting dependency error.",
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


def _select_organizer(client):
    initial = client.get("/session").json()
    changed = client.post(
        "/persona/organizer",
        data={"csrf_token": initial["csrf_token"]},
        headers={"Origin": "http://testserver"},
        follow_redirects=False,
    )
    assert changed.status_code == 303
    return client.get("/session").json()


def _poll_params(html):
    match = re.search(
        r'<section[^>]+id="report-panel"[^>]+data-persona-version="(\d+)"',
        html,
    )
    assert match is not None, "Missing bounded report refresh panel"
    return {"persona_version": match.group(1)}


def test_reports_require_organizer_before_any_reporting_call(monkeypatch, web_environment):
    requests = []

    def respond(request):
        requests.append(request)
        raise AssertionError("A visitor must not access organization reports.")

    with _test_client(monkeypatch, web_environment, respond) as client:
        response = client.get("/reports")

    assert response.status_code == 403
    assert requests == []


def test_report_scope_comes_from_organizer_fixture_not_query_string(monkeypatch, web_environment):
    requests = []

    def respond(request):
        requests.append(request)
        assert request.url.path == f"/organizations/{ORGANIZATION_ID}/reports"
        return httpx.Response(200, json=[_report()])

    with _test_client(monkeypatch, web_environment, respond) as client:
        _select_organizer(client)
        response = client.get("/reports?organizationId=99999999-9999-4999-8999-999999999999")

    assert response.status_code == 200
    assert "Activity report" in response.text
    assert len(requests) == 1
    assert requests[0].url.path == f"/organizations/{ORGANIZATION_ID}/reports"


def test_empty_projection_is_distinct_from_unavailable_or_zero_filled_reports(
    monkeypatch, web_environment
):
    def respond(request):
        return httpx.Response(200, json=[])

    with _test_client(monkeypatch, web_environment, respond) as client:
        _select_organizer(client)
        response = client.get("/reports")

    assert response.status_code == 200
    assert "No report rows are available in this projection yet." in response.text
    assert "Reporting unavailable" not in response.text
    assert "0 confirmed" not in response.text


def test_projected_timestamp_counters_filter_and_pagination_are_observed(
    monkeypatch, web_environment
):
    requests = []

    def respond(request):
        requests.append(request)
        assert request.url.path == f"/organizations/{ORGANIZATION_ID}/reports"
        assert request.url.params["activityId"] == OTHER_ACTIVITY_ID
        assert request.url.params["limit"] == "1"
        assert request.url.params["offset"] == "1"
        return httpx.Response(200, json=[_report(activity_id=OTHER_ACTIVITY_ID)])

    with _test_client(monkeypatch, web_environment, respond) as client:
        _select_organizer(client)
        response = client.get(f"/reports?activityId={OTHER_ACTIVITY_ID}&limit=1&offset=1")

    assert response.status_code == 200
    assert OTHER_ACTIVITY_ID in response.text
    assert "Mar 02, 2027 10:15 UTC" in response.text
    assert "2 confirmed" in response.text
    assert "1 pending payment" in response.text
    assert "1 offered" in response.text
    assert "3 waiting" in response.text
    assert "4 occupied" in response.text
    assert "1 checked in" in response.text
    assert "1 certificate" in response.text
    assert "eventually consistent" in response.text.lower()
    assert len(requests) == 1


def test_reporting_unavailable_does_not_render_success_shaped_zeroes(monkeypatch, web_environment):
    def respond(request):
        return httpx.Response(503, json=_failure())

    with _test_client(monkeypatch, web_environment, respond) as client:
        _select_organizer(client)
        response = client.get("/reports")

    assert response.status_code == 503
    assert "REPORTING_UNAVAILABLE" in response.text
    assert "Reporting unavailable" in response.text
    assert "0 confirmed" not in response.text


def test_unavailable_refresh_retains_last_projection_as_stale(monkeypatch, web_environment):
    requests = []
    responses = [
        httpx.Response(200, json=[_report()]),
        httpx.Response(503, json=_failure()),
        httpx.Response(503, json=_failure()),
    ]

    def respond(request):
        requests.append(request)
        return responses.pop(0)

    with _test_client(monkeypatch, web_environment, respond) as client:
        _select_organizer(client)
        initial = client.get("/reports")
        poll = client.get("/reports/poll", params=_poll_params(initial.text))
        stale_page = client.get("/reports")

    assert initial.status_code == 200
    assert poll.status_code == 503
    assert stale_page.status_code == 200
    assert "Showing the last observed projection, marked stale." in stale_page.text
    assert "4 occupied" in stale_page.text
    assert "stale" in stale_page.text.lower()
    assert poll.json()["state"] == "unavailable"
    assert poll.json()["stale"] is True
    assert poll.json()["reports"][0]["activityId"] == ACTIVITY_ID
    assert poll.json()["error"]["code"] == "REPORTING_UNAVAILABLE"
    assert all(
        "/organizations/" + ORGANIZATION_ID + "/reports" in request.url.path for request in requests
    )
    assert all("/registrations" not in request.url.path for request in requests)


def test_report_refresh_is_bounded_and_independent_of_registration_success(
    monkeypatch, web_environment
):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json=[_report()])

    with _test_client(monkeypatch, web_environment, respond) as client:
        _select_organizer(client)
        page = client.get("/reports")
        params = _poll_params(page.text)
        observed = client.get("/reports/poll", params=params)
        script = client.get("/static/status.js")

    assert page.status_code == 200
    expected_timeout = int(web_environment["DEMO_WEB_POLL_TIMEOUT_SECONDS"]) * 1000
    assert f'data-poll-timeout-ms="{expected_timeout}"' in page.text
    assert observed.status_code == 200
    assert observed.json()["state"] == "observed"
    assert observed.json()["reports"][0]["occupied"] == 4
    assert script.status_code == 200
    assert "report-panel" in script.text
    assert all("/registrations" not in request.url.path for request in requests)
    assert len(requests) == 2
