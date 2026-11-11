from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import subprocess
import time
from uuid import uuid4

import httpx
import pytest


ORGANIZER_TOKEN = "synthetic-organizer-org-a"
PARTICIPANT_TOKEN = "synthetic-participant-001"
SECOND_PARTICIPANT_TOKEN = "synthetic-participant-002"
ORGANIZATION_ID = "30000000-0000-4000-8000-000000000001"
ROOT = Path(__file__).resolve().parents[3]
COMPOSE = [
    "docker",
    "compose",
    "--env-file",
    str(ROOT / "infra/.env"),
    "-f",
    str(ROOT / "infra/docker-compose.yml"),
]


def require_integration_environment() -> dict[str, str]:
    if os.environ.get("EVENTHUB_RUN_FAILURE_INTEGRATION") != "1":
        pytest.skip(
            "Run tests/integration/failure-scenarios/run.sh to enable"
        )
    names = {
        "event_management": "EVENTHUB_EVENT_MANAGEMENT_URL",
        "registration": "EVENTHUB_REGISTRATION_URL",
        "reporting": "EVENTHUB_REPORTING_URL",
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


def create_activity(
    client: httpx.Client,
    urls: dict[str, str],
    run_id,
    correlation_id: str,
    *,
    price_minor: int,
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
        idempotency_key=f"failure-event-{run_id}",
        correlation_id=correlation_id,
        body={
            "organizationId": ORGANIZATION_ID,
            "title": (
                f"[CENÁRIO DE FALHA][SIMULADO][GESAD/UECE] "
                f"Teste de inscrição · {str(run_id)[:8]}"
            ),
            "description": (
                "Evento acadêmico fictício para validação de falhas no "
                "fluxo de inscrição do GESAD/UECE."
            ),
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
        idempotency_key=f"failure-activity-{run_id}",
        correlation_id=correlation_id,
        body={
            "title": (
                f"[CENÁRIO DE FALHA][SIMULADO][GESAD/UECE] "
                f"Atividade de teste · {str(run_id)[:8]}"
            ),
            "startsAt": iso(activity_start),
            "endsAt": iso(activity_end),
            "location": "Espaço simulado de demonstração",
            "responsible": "Equipe simulada do GESAD/UECE",
            "capacity": 1,
            "price": {"amountMinor": price_minor, "currency": "BRL"},
        },
    )
    request_json(
        client,
        "POST",
        f"{urls['event_management']}/events/{event_id}/publish",
        expected=(200,),
        token=ORGANIZER_TOKEN,
        idempotency_key=f"failure-publish-{run_id}",
        correlation_id=correlation_id,
    )
    return activity["id"]


def create_registration(
    client: httpx.Client,
    urls: dict[str, str],
    activity_id: str,
    run_id,
    correlation_id: str,
    *,
    token: str = PARTICIPANT_TOKEN,
) -> dict:
    return request_json(
        client,
        "POST",
        f"{urls['registration']}/registrations",
        expected=(201,),
        token=token,
        idempotency_key=f"failure-registration-{run_id}",
        correlation_id=correlation_id,
        body={"activityId": activity_id},
    )


def get_registration(
    client: httpx.Client,
    urls: dict[str, str],
    registration_id: str,
    correlation_id: str,
    *,
    token: str = PARTICIPANT_TOKEN,
) -> dict:
    return request_json(
        client,
        "GET",
        f"{urls['registration']}/registrations/{registration_id}",
        expected=(200,),
        token=token,
        correlation_id=correlation_id,
    )


def test_refusal_releases_capacity_and_promotes_waitlisted_participant():
    urls = require_integration_environment()
    run_id = uuid4()
    correlation_id = str(uuid4())

    with httpx.Client(timeout=15) as client:
        activity_id = create_activity(
            client, urls, run_id, correlation_id, price_minor=1250
        )
        first = create_registration(
            client, urls, activity_id, run_id, correlation_id
        )
        second_run_id = uuid4()
        waitlisted = create_registration(
            client,
            urls,
            activity_id,
            second_run_id,
            correlation_id,
            token=SECOND_PARTICIPANT_TOKEN,
        )
        assert first["status"] == "PENDING_PAYMENT"
        assert waitlisted["status"] == "WAITLISTED"

        refused = poll_until(
            lambda: get_registration(
                client, urls, first["id"], correlation_id
            ),
            lambda current: current["status"] == "REFUSED",
            "Billing refusal",
        )
        assert refused["status"] == "REFUSED"

        offered = poll_until(
            lambda: get_registration(
                client,
                urls,
                waitlisted["id"],
                correlation_id,
                token=SECOND_PARTICIPANT_TOKEN,
            ),
            lambda current: current["status"] == "OFFERED",
            "waitlist promotion after payment refusal",
        )
        assert offered["status"] == "OFFERED"
        availability = request_json(
            client,
            "GET",
            f"{urls['registration']}/activities/{activity_id}/availability",
            expected=(200,),
            correlation_id=correlation_id,
        )
        assert availability["available"] == 0


def test_payment_timeout_expires_reservation_and_releases_capacity():
    urls = require_integration_environment()
    run_id = uuid4()
    correlation_id = str(uuid4())

    with httpx.Client(timeout=15) as client:
        activity_id = create_activity(
            client, urls, run_id, correlation_id, price_minor=1250
        )
        registration = create_registration(
            client, urls, activity_id, run_id, correlation_id
        )
        assert registration["status"] == "PENDING_PAYMENT"

        expired = poll_until(
            lambda: get_registration(
                client, urls, registration["id"], correlation_id
            ),
            lambda current: current["status"] == "EXPIRED",
            "reservation expiry after Billing timeout",
            timeout_seconds=120,
        )
        assert expired["status"] == "EXPIRED"
        availability = request_json(
            client,
            "GET",
            f"{urls['registration']}/activities/{activity_id}/availability",
            expected=(200,),
            correlation_id=correlation_id,
        )
        assert availability["available"] == 1


def test_notification_unavailable_does_not_block_free_registration():
    urls = require_integration_environment()
    run_id = uuid4()
    correlation_id = str(uuid4())

    with httpx.Client(timeout=15) as client:
        activity_id = create_activity(
            client, urls, run_id, correlation_id, price_minor=0
        )
        registration = create_registration(
            client, urls, activity_id, run_id, correlation_id
        )
        assert registration["status"] == "CONFIRMED"
        confirmed = get_registration(
            client, urls, registration["id"], correlation_id
        )
        assert confirmed["status"] == "CONFIRMED"


def test_reporting_outage_does_not_block_registration_and_recovers():
    urls = require_integration_environment()
    run_id = uuid4()
    correlation_id = str(uuid4())

    with httpx.Client(timeout=15) as client:
        activity_id = create_activity(
            client, urls, run_id, correlation_id, price_minor=0
        )

        stopped = subprocess.run(
            [*COMPOSE, "stop", "reporting"],
            check=False,
            capture_output=True,
            text=True,
        )
        assert stopped.returncode == 0, (
            f"Could not stop Reporting: {stopped.stdout}\n{stopped.stderr}"
        )
        try:
            registration = create_registration(
                client, urls, activity_id, run_id, correlation_id
            )
            assert registration["status"] == "CONFIRMED"
            assert (
                get_registration(
                    client, urls, registration["id"], correlation_id
                )["status"]
                == "CONFIRMED"
            )
        finally:
            started = subprocess.run(
                [
                    *COMPOSE,
                    "up",
                    "--detach",
                    "--wait",
                    "--wait-timeout",
                    "120",
                    "reporting",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            assert started.returncode == 0, (
                f"Could not restart Reporting: {started.stdout}\n{started.stderr}"
            )

        recovered = poll_until(
            lambda: request_json(
                client,
                "GET",
                f"{urls['reporting']}/organizations/{ORGANIZATION_ID}/reports"
                f"?activityId={activity_id}",
                expected=(200,),
                token=ORGANIZER_TOKEN,
                correlation_id=correlation_id,
            ),
            lambda rows: bool(rows) and rows[0]["confirmed"] == 1,
            "Reporting recovery and durable registration projection",
        )
        assert recovered[0]["activityId"] == activity_id
