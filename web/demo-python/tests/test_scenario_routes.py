from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient

from demo_web.app import create_app


ROOT = Path(__file__).resolve().parents[3]
SCENARIO_FIXTURES = ROOT / "mocks/identity-access/scenario-participants.json"
RUN_ID = UUID("70000000-0000-4000-8000-000000000001")


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


class StubScenarioManager:
    def __init__(self):
        self.calls = []
        self.active_owner = None
        self.owner = None

    async def shutdown(self):
        return None

    def is_running_for(self, owner):
        return self.active_owner == owner

    def preview(self, preset):
        return {
            "preset": preset,
            "participants": 2,
            "available": True,
        }

    def snapshots_for(self, owner):
        return [{"id": str(RUN_ID)}] if owner == self.owner else []

    def snapshot(self, owner, run_id):
        if owner == self.owner and run_id == str(RUN_ID):
            return {"id": run_id, "verdict": "INCONCLUSIVE"}
        return None

    def export(self, owner, run_id):
        snapshot = self.snapshot(owner, run_id)
        if snapshot is None:
            return None
        return {"schemaVersion": 1, "run": snapshot}

    async def start(self, owner, preset):
        self.owner = owner
        self.calls.append(("start", owner, preset))
        return {"id": str(RUN_ID), "state": "RUNNING"}

    async def stop(self, owner, run_id):
        self.calls.append(("stop", owner, run_id))
        return {"id": run_id, "state": "STOPPED"}

    async def cancel_confirmed(self, owner, run_id):
        self.calls.append(("cancel", owner, run_id))
        return {"id": run_id, "fifo": {"verdict": "EXPECTED"}}

    async def accept_offer(self, owner, run_id):
        self.calls.append(("accept", owner, run_id))
        return {"id": run_id, "fifo": {"verdict": "EXPECTED"}}


def _select_organizer(client):
    initial = client.get("/session").json()
    changed = client.post(
        "/persona/organizer",
        headers={
            "Origin": "http://testserver",
            "X-CSRF-Token": initial["csrf_token"],
        },
        follow_redirects=False,
    )
    assert changed.status_code == 303
    return client.get("/session").json()["csrf_token"]


def test_scenario_routes_require_organizer_csrf_and_session_ownership(
    web_environment,
):
    app = create_app(_enabled_lab(web_environment))
    with TestClient(app) as client:
        actual_manager = app.state.scenario_manager
        manager = StubScenarioManager()
        app.state.scenario_manager = manager

        assert client.get("/scenario-lab/presets/last-seat").status_code == 403
        client.get("/session")
        missing_csrf = client.post("/scenario-lab/runs/last-seat")
        assert missing_csrf.status_code == 403

        csrf = _select_organizer(client)
        assert client.get("/scenario-lab/presets/last-seat").json()["available"]
        headers = {
            "Origin": "http://testserver",
            "X-CSRF-Token": csrf,
        }

        started = client.post(
            "/scenario-lab/runs/last-seat",
            headers=headers,
        )
        assert started.status_code == 202
        assert started.json()["id"] == str(RUN_ID)
        assert client.get("/scenario-lab/runs").json()["runs"] == [{"id": str(RUN_ID)}]
        assert client.get(f"/scenario-lab/runs/{RUN_ID}").status_code == 200
        exported = client.get(f"/scenario-lab/runs/{RUN_ID}/export")
        assert exported.status_code == 200
        assert "scenarioParticipant" not in exported.text
        assert "private-scenario-token" not in exported.text
        assert (
            client.post(
                f"/scenario-lab/runs/{RUN_ID}/stop",
                headers=headers,
            ).status_code
            == 200
        )
        assert (
            client.post(
                f"/scenario-lab/runs/{RUN_ID}/cancel-confirmed",
                headers=headers,
            ).status_code
            == 200
        )
        assert (
            client.post(
                f"/scenario-lab/runs/{RUN_ID}/accept-offer",
                headers=headers,
            ).status_code
            == 200
        )

        owner_cookie = client.cookies.get("demo_session")
        manager.active_owner = owner_cookie
        blocked_persona = client.post(
            "/persona/participant",
            headers=headers,
            follow_redirects=False,
        )
        assert blocked_persona.status_code == 409
        manager.active_owner = None

        client.cookies.clear()
        _select_organizer(client)
        assert client.get(f"/scenario-lab/runs/{RUN_ID}").status_code == 404
        assert client.get(f"/scenario-lab/runs/{RUN_ID}/export").status_code == 404
        app.state.scenario_manager = actual_manager

    assert [call[0] for call in manager.calls] == [
        "start",
        "stop",
        "cancel",
        "accept",
    ]
