from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import os
from threading import Barrier, Event
import time
from uuid import uuid4

import httpx
import pytest


ORGANIZER_TOKEN = "synthetic-organizer-org-a"
PARTICIPANTS = (
    "synthetic-participant-001",
    "synthetic-participant-002",
)
ORGANIZATION_ID = "30000000-0000-4000-8000-000000000001"


def integration_urls() -> dict[str, str]:
    if os.environ.get("EVENTHUB_RUN_CONCURRENCY") != "1":
        pytest.skip("Run tests/concurrency/run.sh to enable")
    names = {
        "event_management": "EVENTHUB_EVENT_MANAGEMENT_URL",
        "registration": "EVENTHUB_REGISTRATION_URL",
    }
    return {service: os.environ[name] for service, name in names.items()}


def request_json(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    expected: tuple[int, ...],
    correlation_id: str,
    token: str | None = None,
    idempotency_key: str | None = None,
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


def poll_until(read_value, ready, description: str, *, timeout_seconds: int = 30):
    deadline = time.monotonic() + timeout_seconds
    latest = None
    while time.monotonic() < deadline:
        latest = read_value()
        if ready(latest):
            return latest
        time.sleep(0.5)
    pytest.fail(f"Timed out waiting for {description}; latest value: {latest}")


def create_capacity_one_activity(
    client: httpx.Client,
    urls: dict[str, str],
    run_id,
    correlation_id: str,
) -> str:
    now = datetime.now(timezone.utc)
    event_start = now + timedelta(minutes=10)
    activity_start = event_start + timedelta(minutes=1)
    activity_end = activity_start + timedelta(hours=1)

    def iso(value: datetime) -> str:
        return value.isoformat(timespec="seconds").replace("+00:00", "Z")

    event = request_json(
        client,
        "POST",
        f"{urls['event_management']}/events",
        expected=(201,),
        token=ORGANIZER_TOKEN,
        idempotency_key=f"concurrency-event-{run_id}",
        correlation_id=correlation_id,
        body={
            "organizationId": ORGANIZATION_ID,
            "title": f"Concurrency test {run_id}",
            "description": "Synthetic capacity contention integration test",
            "startsAt": iso(event_start),
            "endsAt": iso(activity_end + timedelta(minutes=1)),
        },
    )
    event_id = event["id"]
    activity = request_json(
        client,
        "POST",
        f"{urls['event_management']}/events/{event_id}/activities",
        expected=(201,),
        token=ORGANIZER_TOKEN,
        idempotency_key=f"concurrency-activity-{run_id}",
        correlation_id=correlation_id,
        body={
            "title": f"Single-slot activity {run_id}",
            "startsAt": iso(activity_start),
            "endsAt": iso(activity_end),
            "location": "Synthetic concurrency venue",
            "responsible": "Synthetic organizer",
            "capacity": 1,
            "price": {"amountMinor": 0, "currency": "BRL"},
        },
    )
    request_json(
        client,
        "POST",
        f"{urls['event_management']}/events/{event_id}/publish",
        expected=(200,),
        token=ORGANIZER_TOKEN,
        idempotency_key=f"concurrency-publish-{run_id}",
        correlation_id=correlation_id,
    )
    return activity["id"]


def submit_at_barrier(
    barrier: Barrier,
    urls: dict[str, str],
    activity_id: str,
    participant_token: str,
    correlation_id: str,
    idempotency_key: str,
) -> tuple[str, dict]:
    with httpx.Client(timeout=30) as client:
        barrier.wait(timeout=15)
        registration = request_json(
            client,
            "POST",
            f"{urls['registration']}/registrations",
            expected=(201,),
            token=participant_token,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
            body={"activityId": activity_id},
        )
    return participant_token, registration


def sample_capacity(
    stop: Event,
    urls: dict[str, str],
    activity_id: str,
    correlation_id: str,
) -> list[dict]:
    observations = []
    with httpx.Client(timeout=30) as client:
        while not stop.is_set():
            observations.append(
                request_json(
                    client,
                    "GET",
                    f"{urls['registration']}/activities/{activity_id}/availability",
                    expected=(200,),
                    correlation_id=correlation_id,
                )
            )
            stop.wait(0.01)
    return observations


def test_concurrent_requests_never_exceed_capacity_and_promotion_preserves_it():
    urls = integration_urls()
    run_id = uuid4()
    correlation_id = str(uuid4())

    with httpx.Client(timeout=15) as client:
        activity_id = create_capacity_one_activity(
            client, urls, run_id, correlation_id
        )

        barrier = Barrier(len(PARTICIPANTS))
        stop_sampling = Event()
        with ThreadPoolExecutor(max_workers=len(PARTICIPANTS) + 1) as executor:
            sample = executor.submit(
                sample_capacity,
                stop_sampling,
                urls,
                activity_id,
                correlation_id,
            )
            requests = [
                executor.submit(
                    submit_at_barrier,
                    barrier,
                    urls,
                    activity_id,
                    participant_token,
                    str(uuid4()),
                    f"concurrency-registration-{run_id}-{index}",
                )
                for index, participant_token in enumerate(PARTICIPANTS)
            ]
            try:
                results = [request.result(timeout=45) for request in requests]
            finally:
                stop_sampling.set()
            observations = sample.result(timeout=45)

        assert observations
        assert all(
            snapshot["capacity"] == 1
            and snapshot["occupied"] <= snapshot["capacity"]
            and snapshot["available"]
            == snapshot["capacity"] - snapshot["occupied"]
            for snapshot in observations
        )

        statuses = [registration["status"] for _, registration in results]
        assert statuses.count("CONFIRMED") == 1
        assert statuses.count("WAITLISTED") == 1
        assert sum(status in {"CONFIRMED", "OFFERED"} for status in statuses) <= 1

        availability = request_json(
            client,
            "GET",
            f"{urls['registration']}/activities/{activity_id}/availability",
            expected=(200,),
            correlation_id=correlation_id,
        )
        assert availability["capacity"] == 1
        assert availability["occupied"] == 1
        assert availability["available"] == 0

        confirmed_token, confirmed = next(
            result for result in results if result[1]["status"] == "CONFIRMED"
        )
        waitlisted_token, waitlisted = next(
            result for result in results if result[1]["status"] == "WAITLISTED"
        )
        cancelled = request_json(
            client,
            "POST",
            f"{urls['registration']}/registrations/{confirmed['id']}/cancel",
            expected=(200,),
            token=confirmed_token,
            idempotency_key=f"concurrency-cancel-{run_id}",
            correlation_id=correlation_id,
        )
        assert cancelled["status"] == "CANCELLED"

        offered = poll_until(
            lambda: request_json(
                client,
                "GET",
                f"{urls['registration']}/registrations/{waitlisted['id']}",
                expected=(200,),
                token=waitlisted_token,
                correlation_id=correlation_id,
            ),
            lambda current: current["status"] == "OFFERED",
            "waitlist promotion after concurrent winner cancellation",
        )
        assert offered["status"] == "OFFERED"
        assert sum(
            registration["status"] in {"CONFIRMED", "OFFERED"}
            for registration in (cancelled, offered)
        ) == 1

        final_availability = request_json(
            client,
            "GET",
            f"{urls['registration']}/activities/{activity_id}/availability",
            expected=(200,),
            correlation_id=correlation_id,
        )
        assert final_availability["capacity"] == 1
        assert final_availability["occupied"] == 1
        assert final_availability["available"] == 0
