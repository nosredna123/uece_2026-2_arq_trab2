from datetime import datetime, timedelta, timezone
import os
import time
from uuid import uuid4

import httpx
import pytest


ORGANIZER_TOKEN = "synthetic-organizer-org-a"
PARTICIPANT_TOKEN = "synthetic-participant-001"
ORGANIZATION_ID = "30000000-0000-4000-8000-000000000001"


def require_integration_environment() -> dict[str, str]:
    if os.environ.get("EVENTHUB_RUN_INTEGRATION") != "1":
        pytest.skip("Run tests/integration/paid-registration-flow/run.sh to enable")
    names = {
        "event_management": "EVENTHUB_EVENT_MANAGEMENT_URL",
        "registration": "EVENTHUB_REGISTRATION_URL",
        "attendance": "EVENTHUB_ATTENDANCE_URL",
        "reporting": "EVENTHUB_REPORTING_URL",
    }
    return {service: os.environ[name] for service, name in names.items()}


def request_json(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    expected: tuple[int, ...],
    token: str | None = None,
    idempotency_key: str | None = None,
    correlation_id: str,
    body: dict | None = None,
) -> dict | list:
    headers = {"X-Correlation-ID": correlation_id}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    response = client.request(method, url, headers=headers, json=body)
    assert response.status_code in expected, (
        f"{method} {url} returned {response.status_code}: {response.text}"
    )
    return response.json()


def poll_until(
    read_value,
    ready,
    description: str,
    *,
    timeout_seconds: int = 60,
):
    deadline = time.monotonic() + timeout_seconds
    latest = None
    while time.monotonic() < deadline:
        latest = read_value()
        if ready(latest):
            return latest
        time.sleep(1)
    pytest.fail(f"Timed out waiting for {description}; latest value: {latest}")


def test_paid_registration_checkin_certificate_and_report():
    urls = require_integration_environment()
    run_id = uuid4()
    correlation_id = str(uuid4())
    event_start = datetime.now(timezone.utc) + timedelta(seconds=45)
    activity_start = event_start + timedelta(seconds=5)
    activity_end = activity_start + timedelta(seconds=15)
    event_end = activity_end + timedelta(minutes=1)

    def iso(value: datetime) -> str:
        return value.isoformat(timespec="seconds").replace("+00:00", "Z")

    with httpx.Client(timeout=15) as client:
        event = request_json(
            client,
            "POST",
            f"{urls['event_management']}/events",
            expected=(201,),
            token=ORGANIZER_TOKEN,
            idempotency_key=f"paid-flow-event-{run_id}",
            correlation_id=correlation_id,
            body={
                "organizationId": ORGANIZATION_ID,
                "title": f"Paid flow {run_id}",
                "description": "Synthetic integration event",
                "startsAt": iso(event_start),
                "endsAt": iso(event_end),
            },
        )
        event_id = event["id"]
        activity = request_json(
            client,
            "POST",
            f"{urls['event_management']}/events/{event_id}/activities",
            expected=(201,),
            token=ORGANIZER_TOKEN,
            idempotency_key=f"paid-flow-activity-{run_id}",
            correlation_id=correlation_id,
            body={
                "title": f"Paid activity {run_id}",
                "startsAt": iso(activity_start),
                "endsAt": iso(activity_end),
                "location": "Synthetic integration venue",
                "responsible": "Synthetic organizer",
                "capacity": 1,
                "price": {"amountMinor": 1250, "currency": "BRL"},
            },
        )
        activity_id = activity["id"]
        published = request_json(
            client,
            "POST",
            f"{urls['event_management']}/events/{event_id}/publish",
            expected=(200,),
            token=ORGANIZER_TOKEN,
            idempotency_key=f"paid-flow-publish-{run_id}",
            correlation_id=correlation_id,
        )
        assert published["status"] == "PUBLISHED"

        catalog = request_json(
            client,
            "GET",
            f"{urls['event_management']}/events?q={run_id}",
            expected=(200,),
            correlation_id=correlation_id,
        )
        assert any(item["id"] == event_id for item in catalog)
        program = request_json(
            client,
            "GET",
            f"{urls['event_management']}/events/{event_id}/activities",
            expected=(200,),
            correlation_id=correlation_id,
        )
        assert any(item["id"] == activity_id for item in program)

        availability = request_json(
            client,
            "GET",
            f"{urls['registration']}/activities/{activity_id}/availability",
            expected=(200,),
            correlation_id=correlation_id,
        )
        assert availability["capacity"] == 1
        assert availability["available"] == 1

        registration = request_json(
            client,
            "POST",
            f"{urls['registration']}/registrations",
            expected=(201,),
            token=PARTICIPANT_TOKEN,
            idempotency_key=f"paid-flow-registration-{run_id}",
            correlation_id=correlation_id,
            body={"activityId": activity_id},
        )
        registration_id = registration["id"]
        assert registration["status"] == "PENDING_PAYMENT"

        confirmed = poll_until(
            lambda: request_json(
                client,
                "GET",
                f"{urls['registration']}/registrations/{registration_id}",
                expected=(200,),
                token=PARTICIPANT_TOKEN,
                correlation_id=correlation_id,
            ),
            lambda current: current["status"] == "CONFIRMED",
            "approved Billing result to confirm registration",
        )
        assert confirmed["status"] == "CONFIRMED"

        time.sleep(
            max(0, (activity_end - datetime.now(timezone.utc)).total_seconds()) + 2
        )

        check_in = poll_until(
            lambda: request_json(
                client,
                "POST",
                f"{urls['attendance']}/registrations/{registration_id}/check-in",
                expected=(200, 409),
                token=PARTICIPANT_TOKEN,
                idempotency_key=f"paid-flow-check-in-{run_id}",
                correlation_id=correlation_id,
            ),
            lambda response: response.get("registrationId") == registration_id,
            "Attendance to project eligibility and record check-in",
        )
        assert check_in["registrationId"] == registration_id

        certificate = poll_until(
            lambda: request_json(
                client,
                "POST",
                f"{urls['attendance']}/registrations/{registration_id}/certificate",
                expected=(200, 409),
                token=PARTICIPANT_TOKEN,
                idempotency_key=f"paid-flow-certificate-{run_id}",
                correlation_id=correlation_id,
            ),
            lambda response: response.get("registrationId") == registration_id,
            "Attendance to issue the eligible certificate",
        )
        assert certificate["registrationId"] == registration_id
        assert certificate["activityId"] == activity_id
        retrieved = request_json(
            client,
            "GET",
            f"{urls['attendance']}/registrations/{registration_id}/certificate",
            expected=(200,),
            token=PARTICIPANT_TOKEN,
            correlation_id=correlation_id,
        )
        assert retrieved["id"] == certificate["id"]

        report = poll_until(
            lambda: request_json(
                client,
                "GET",
                f"{urls['reporting']}/organizations/{ORGANIZATION_ID}/reports"
                f"?activityId={activity_id}",
                expected=(200,),
                token=ORGANIZER_TOKEN,
                correlation_id=correlation_id,
            ),
            lambda rows: bool(rows)
            and rows[0]["confirmed"] == 1
            and rows[0]["paymentsApproved"] == 1
            and rows[0]["checkedIn"] == 1
            and rows[0]["certificates"] == 1,
            "Reporting to project the complete paid-registration flow",
        )
        assert report[0]["activityId"] == activity_id
