import re
from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient

from demo_web.app import create_app


ROOT = Path(__file__).resolve().parents[3]
SCENARIO_FIXTURES = ROOT / "mocks/identity-access/scenario-participants.json"
RUN_ID = UUID("71000000-0000-4000-8000-000000000001")
OBSERVED_AT = "2026-11-10T12:00:00+00:00"


def _enabled_lab(web_environment):
    return {
        **web_environment,
        "IDENTITY_LAB_ENABLED": "true",
        "DEMO_LAB_ENABLED": "true",
        "DEMO_LAB_FIXTURES_PATH": str(SCENARIO_FIXTURES),
        "DEMO_LAB_MAX_PARTICIPANTS": "50",
        "DEMO_LAB_MAX_CONCURRENCY": "10",
        "DEMO_LAB_RUN_TIMEOUT_SECONDS": "60",
        "DEMO_LAB_SETTLE_TIMEOUT_SECONDS": "30",
        "DEMO_LAB_RUN_TTL_SECONDS": "1800",
        "DEMO_LAB_MAX_RETAINED_RUNS": "5",
    }


def _snapshot(*, state="COMPLETED"):
    return {
        "id": str(RUN_ID),
        "preset": "registration-rush",
        "title": "Registration rush",
        "state": state,
        "verdict": "MISMATCH",
        "createdAt": OBSERVED_AT,
        "elapsedMs": 240,
        "eventId": "72000000-0000-4000-8000-000000000001",
        "activityId": "73000000-0000-4000-8000-000000000001",
        "capacity": 5,
        "participantCount": 20,
        "workload": {
            "dispatchCount": 20,
            "completedCount": 20,
            "peakInFlight": 10,
            "errors": 1,
            "unknown": 1,
            "throughputPerSecond": 83.3,
        },
        "sampleRequests": 21,
        "sampleBudget": 21,
        "readback": {
            "observedAt": OBSERVED_AT,
            "availability": {"capacity": 5, "occupied": 8, "available": 0},
            "registrationCount": 20,
            "statuses": ["CONFIRMED"] * 8 + ["WAITLISTED"] * 12,
            "sampleErrors": 0,
        },
        "observations": [
            {
                "participant": "Participant 01",
                "state": "unknown",
                "status": None,
                "registrationId": None,
                "correlationId": None,
                "errorCode": "HTTP_TIMEOUT",
                "latencyMs": 100,
            },
            {
                "participant": "Participant 02",
                "state": "error",
                "status": None,
                "registrationId": None,
                "correlationId": None,
                "errorCode": "WAITLIST_DISABLED",
                "latencyMs": 20,
            },
        ],
        "timeline": [
            {
                "participant": "Participant 01",
                "state": "unknown",
                "status": None,
                "registrationId": None,
                "errorCode": "HTTP_TIMEOUT",
                "latencyMs": 100,
                "at": OBSERVED_AT,
            }
        ],
        "errors": ["WAITLIST_DISABLED"],
        "fifo": {
            "expectedRegistrationId": "74000000-0000-4000-8000-000000000001",
            "offeredRegistrationId": None,
            "acceptedRegistrationId": None,
            "verdict": "NOT_RUN",
        },
    }


class StubScenarioManager:
    def __init__(self):
        self.runs = []
        self.start_calls = 0
        self.active_owner = None

    def preview(self, preset):
        specs = {
            "last-seat": (2, 1, 2),
            "registration-rush": (20, 5, 10),
            "duplicate-submission": (1, 1, 10),
        }
        participants, capacity, concurrency = specs[preset]
        return {
            "preset": preset,
            "title": preset,
            "participants": participants,
            "capacity": capacity,
            "submissionsPerParticipant": 10 if preset == "duplicate-submission" else 1,
            "concurrency": concurrency,
            "priceMinor": 0,
            "available": True,
            "unavailableReason": None,
        }

    def snapshots_for(self, owner):
        return list(self.runs) if self.active_owner in {None, owner} else []

    def is_running_for(self, owner):
        return self.active_owner == owner

    async def shutdown(self):
        return None


def _select_organizer(client):
    initial = client.get("/session").json()
    response = client.post(
        "/persona/organizer",
        headers={
            "Origin": "http://testserver",
            "X-CSRF-Token": initial["csrf_token"],
        },
        follow_redirects=False,
    )
    assert response.status_code == 303


def _test_client(web_environment):
    app = create_app(_enabled_lab(web_environment))
    client = TestClient(app)
    client.__enter__()
    manager = StubScenarioManager()
    app.state.scenario_manager = manager
    return app, client, manager


def test_lab_page_renders_fixed_predictions_and_provenance_without_mutations(
    web_environment,
):
    app, client, manager = _test_client(web_environment)
    try:
        _select_organizer(client)
        response = client.get("/scenario-lab")
        home = client.get("/")
    finally:
        actual_manager = app.state.scenario_manager
        if actual_manager is manager:
            app.state.scenario_manager = None
        client.__exit__(None, None, None)

    html = response.text
    assert response.status_code == 200
    assert 'href="/scenario-lab"' in home.text
    for preset in ("last-seat", "registration-rush", "duplicate-submission"):
        assert f'data-preset="{preset}"' in html
    assert 'data-preset="seat-opens"' in html
    assert "2 participants" in html
    assert "20 participants" in html
    assert "5 free seats" in html
    assert "10 concurrent submissions" in html
    assert "Prediction" in html
    assert "SIMULATED DEPENDENCY" in html
    assert "DEMO PRESENTATION" in html
    assert "LIVE SERVICE" in html
    assert manager.start_calls == 0


def test_preset_cards_expose_the_fixed_workload_values(web_environment):
    app, client, manager = _test_client(web_environment)
    try:
        response = client.get("/scenario-lab")
    finally:
        app.state.scenario_manager = None
        client.__exit__(None, None, None)

    assert response.status_code == 200
    expected = {
        "last-seat": (2, 1, 2, 1),
        "registration-rush": (20, 5, 10, 1),
        "duplicate-submission": (1, 1, 10, 10),
        "seat-opens": (0, 0, 0, 0),
    }
    for preset, (participants, capacity, concurrency, submissions) in expected.items():
        assert re.search(
            rf'<article class="lab-preset-card"\s+data-preset="{preset}"'
            rf'\s+data-participants="{participants}"\s+data-capacity="{capacity}"'
            rf'\s+data-concurrency="{concurrency}"'
            rf'\s+data-submissions-per-participant="{submissions}"',
            response.text,
        )
    assert manager.start_calls == 0


def test_disabled_lab_is_preview_only(web_environment):
    with TestClient(create_app(web_environment)) as client:
        response = client.get("/scenario-lab")

    assert response.status_code == 200
    assert "Scenario Lab is disabled." in response.text
    assert "Scenario Lab is disabled by configuration." in response.text
    assert "data-run-preset=" not in response.text
    assert "data-run-action=" not in response.text


def test_lab_board_keeps_anomalies_and_unknown_outcomes_visible(web_environment):
    app, client, manager = _test_client(web_environment)
    manager.runs = [_snapshot()]
    try:
        _select_organizer(client)
        response = client.get("/scenario-lab")
    finally:
        app.state.scenario_manager = None
        client.__exit__(None, None, None)

    html = response.text
    assert response.status_code == 200
    assert "Occupied" in html
    assert "8" in html
    assert "exceeds capacity" in html.lower()
    assert "Unknown outcome" in html
    assert "Request error" in html
    assert "INCONCLUSIVE" in html or "MISMATCH" in html
    assert "HTTP_TIMEOUT" in html
    assert "WAITLIST_DISABLED" in html


def test_active_run_has_safe_stop_and_refresh_does_not_restart(web_environment):
    app, client, manager = _test_client(web_environment)
    active = _snapshot(state="RUNNING")
    manager.runs = [active]
    try:
        _select_organizer(client)
        first = client.get("/scenario-lab")
        refreshed = client.get("/scenario-lab")
    finally:
        app.state.scenario_manager = None
        client.__exit__(None, None, None)

    assert first.status_code == refreshed.status_code == 200
    assert "Stop new requests and drain in-flight work" in first.text
    assert str(RUN_ID) in refreshed.text
    assert manager.start_calls == 0


def test_non_organizer_sees_preview_but_no_mutation_controls(web_environment):
    app, client, manager = _test_client(web_environment)
    try:
        response = client.get("/scenario-lab")
    finally:
        app.state.scenario_manager = None
        client.__exit__(None, None, None)

    assert response.status_code == 200
    assert "last-seat" in response.text
    assert "Prepare and run" not in response.text
    assert manager.start_calls == 0


def test_lab_assets_have_reduced_motion_and_bounded_polling(web_environment):
    with TestClient(create_app(_enabled_lab(web_environment))) as client:
        stylesheet = client.get("/static/app.css")
        script = client.get("/static/lab.js")

    assert stylesheet.status_code == script.status_code == 200
    assert "@media (prefers-reduced-motion: reduce)" in stylesheet.text
    assert "MAX_POLL_ATTEMPTS" in script.text
    assert "setInterval" not in script.text
