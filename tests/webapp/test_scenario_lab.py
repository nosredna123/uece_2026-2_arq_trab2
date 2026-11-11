import http.cookiejar
import json
import os
from pathlib import Path
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import (
    HTTPCookieProcessor,
    Request,
    build_opener,
)

import pytest


BASE_URL = os.environ.get("SCENARIO_LAB_BASE_URL")
ARTIFACTS_DIR = Path(os.environ.get("WEBAPP_TEST_ARTIFACTS_DIR", "/tmp/webapp-artifacts"))


class ScenarioLabClient:
    def __init__(self, base_url):
        self.base_url = base_url.rstrip("/")
        self.origin = f"{urlsplit(self.base_url).scheme}://{urlsplit(self.base_url).netloc}"
        self.opener = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.csrf_token = None

    def request(self, method, path, *, csrf=False):
        headers = {"Accept": "application/json"}
        data = b""
        if csrf:
            headers["Origin"] = self.origin
            headers["X-CSRF-Token"] = self.csrf_token
        request = Request(
            f"{self.base_url}{path}",
            data=data if method != "GET" else None,
            headers=headers,
            method=method,
        )
        try:
            response = self.opener.open(request, timeout=10)
        except HTTPError as error:
            return error.code, json.loads(error.read().decode("utf-8"))
        body = response.read()
        if response.headers.get_content_type() != "application/json":
            return response.status, {}
        return response.status, json.loads(body.decode("utf-8")) if body else {}

    def select_organizer(self):
        status, session = self.request("GET", "/session")
        assert status == 200
        self.csrf_token = session["csrf_token"]
        status, _ = self.request("POST", "/persona/organizer", csrf=True)
        assert status == 200
        status, session = self.request("GET", "/session")
        assert status == 200
        self.csrf_token = session["csrf_token"]

    def start_and_wait(self, preset):
        status, started = self.request(
            "POST",
            f"/scenario-lab/runs/{preset}",
            csrf=True,
        )
        assert status == 202
        deadline = time.monotonic() + 70
        while time.monotonic() < deadline:
            status, snapshot = self.request(
                "GET",
                f"/scenario-lab/runs/{started['id']}",
            )
            assert status == 200
            if snapshot["state"] not in {"PREPARING", "RUNNING"}:
                return snapshot
            time.sleep(0.25)
        raise AssertionError("Scenario Lab run did not settle within the configured bound.")

    def save_export(self, snapshot, filename):
        status, exported = self.request(
            "GET",
            f"/scenario-lab/runs/{snapshot['id']}/export",
        )
        assert status == 200
        contents = json.dumps(exported, indent=2, sort_keys=True)
        assert "token" not in contents.lower()
        ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
        (ARTIFACTS_DIR / filename).write_text(contents + "\n", encoding="utf-8")


@pytest.mark.skipif(
    not BASE_URL,
    reason="Set SCENARIO_LAB_BASE_URL to run against an explicitly provisioned service stack.",
)
def test_real_scenario_lab_readback_replay_and_fifo_continuation():
    organizer = ScenarioLabClient(BASE_URL)
    other_session = ScenarioLabClient(BASE_URL)
    organizer.select_organizer()
    other_session.select_organizer()

    last_seat = organizer.start_and_wait("last-seat")
    assert last_seat["state"] == "COMPLETED"
    assert last_seat["workload"]["dispatchCount"] == 2
    assert last_seat["participantCount"] == 2
    assert last_seat["readback"]["registrationCount"] == 2
    statuses = last_seat["readback"]["statuses"]
    assert statuses.count("CONFIRMED") == 1
    assert statuses.count("WAITLISTED") == 1
    assert last_seat["verdict"] == "EXPECTED"

    hidden_status, _ = other_session.request(
        "GET",
        f"/scenario-lab/runs/{last_seat['id']}",
    )
    assert hidden_status == 404

    canceled_status, canceled = organizer.request(
        "POST",
        f"/scenario-lab/runs/{last_seat['id']}/cancel-confirmed",
        csrf=True,
    )
    assert canceled_status == 200
    expected_offer = canceled["fifo"]["expectedRegistrationId"]
    assert expected_offer is not None
    assert canceled["fifo"]["offeredRegistrationId"] == expected_offer
    assert canceled["fifo"]["verdict"] == "EXPECTED"

    accepted_status, accepted = organizer.request(
        "POST",
        f"/scenario-lab/runs/{last_seat['id']}/accept-offer",
        csrf=True,
    )
    assert accepted_status == 200
    assert accepted["fifo"]["acceptedRegistrationId"] == expected_offer
    assert accepted["fifo"]["verdict"] == "EXPECTED"
    organizer.save_export(accepted, "scenario-last-seat.json")

    rush = organizer.start_and_wait("registration-rush")
    assert rush["state"] == "COMPLETED"
    assert rush["workload"]["dispatchCount"] == 20
    assert rush["readback"]["availability"]["occupied"] == 5
    assert rush["readback"]["statuses"].count("CONFIRMED") == 5
    assert rush["readback"]["statuses"].count("WAITLISTED") == 15
    assert rush["verdict"] == "EXPECTED"
    organizer.save_export(rush, "scenario-registration-rush.json")

    duplicate = organizer.start_and_wait("duplicate-submission")
    returned_ids = {
        item["registrationId"]
        for item in duplicate["observations"]
        if item["registrationId"] is not None
    }
    assert duplicate["workload"]["dispatchCount"] == 10
    assert len(returned_ids) == 1
    assert duplicate["readback"]["registrationCount"] == 1
    assert duplicate["verdict"] == "EXPECTED"
    organizer.save_export(duplicate, "scenario-duplicate-submission.json")
