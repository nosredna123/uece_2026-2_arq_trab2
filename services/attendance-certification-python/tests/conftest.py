from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient


PARTICIPANT_ID = UUID("30000000-0000-0000-0000-000000000001")
OTHER_PARTICIPANT_ID = UUID("30000000-0000-0000-0000-000000000002")
ACTIVITY_ID = UUID("40000000-0000-0000-0000-000000000001")
ORGANIZATION_ID = UUID("20000000-0000-0000-0000-000000000001")
REGISTRATION_ID = UUID("50000000-0000-0000-0000-000000000001")


class FakeIdentityClient:
    def __init__(self) -> None:
        self.principals = {
            "participant-token": SimpleNamespace(
                subject_id=PARTICIPANT_ID,
                role="PARTICIPANT",
                organization_id=None,
            ),
            "other-participant-token": SimpleNamespace(
                subject_id=OTHER_PARTICIPANT_ID,
                role="PARTICIPANT",
                organization_id=None,
            ),
            "organizer-token": SimpleNamespace(
                subject_id=UUID("60000000-0000-0000-0000-000000000001"),
                role="ORGANIZER",
                organization_id=ORGANIZATION_ID,
            ),
        }

    def validate(self, token: str, correlation_id: str):
        return self.principals.get(token)


@pytest.fixture
def attendance(monkeypatch, tmp_path):
    database_path = tmp_path / "attendance.db"
    values = {
        "DATABASE_URL": f"sqlite:///{database_path}",
        "IDENTITY_BASE_URL": "http://identity-mock:8080",
        "INTERNAL_SERVICE_KEY": "synthetic-unit-only",
        "HTTP_TIMEOUT_SECONDS": "5",
        "MESSAGE_RETRY_INITIAL_SECONDS": "1",
        "MESSAGE_RETRY_MAX_SECONDS": "60",
        "CERTIFICATE_ELIGIBILITY_POLICY": "CHECK_IN",
        "CERTIFICATE_ISSUANCE_AFTER_EVENT": "true",
        "RABBITMQ_HOST": "localhost",
        "RABBITMQ_PORT": "5672",
        "RABBITMQ_USER": "synthetic",
        "RABBITMQ_PASSWORD": "synthetic",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)

    from attendance_service.app import create_app
    from attendance_service.database import metadata

    identity_client = FakeIdentityClient()
    app = create_app(identity_client=identity_client, start_worker=False)
    metadata.create_all(app.state.engine)
    with TestClient(app) as client:
        yield SimpleNamespace(
            client=client,
            app=app,
            identity=identity_client,
            engine=app.state.engine,
        )


def registration_event(
    *,
    status: str = "CONFIRMED",
    version: int = 1,
    activity_ends_at: datetime | None = None,
    registration_id: UUID = REGISTRATION_ID,
) -> dict:
    ends_at = activity_ends_at or datetime.now(UTC) - timedelta(minutes=1)
    return {
        "eventId": str(uuid4()),
        "eventType": "registration.changed",
        "schemaVersion": "1.0.0",
        "aggregateId": str(registration_id),
        "aggregateVersion": version,
        "correlationId": str(uuid4()),
        "occurredAt": datetime.now(UTC).isoformat(),
        "producer": "registration",
        "data": {
            "registrationId": str(registration_id),
            "activityId": str(ACTIVITY_ID),
            "organizationId": str(ORGANIZATION_ID),
            "participantId": str(PARTICIPANT_ID),
            "status": status,
            "refundStatus": "NONE",
            "amountMinor": 0,
            "currency": "USD",
            "activityStartsAt": (datetime.now(UTC) - timedelta(hours=2)).isoformat(),
            "activityEndsAt": ends_at.isoformat(),
        },
    }


def activity_event(
    *,
    ends_at: datetime | None = None,
    version: int = 1,
    status: str = "PUBLISHED",
) -> dict:
    return {
        "eventId": str(uuid4()),
        "eventType": "activity.changed",
        "schemaVersion": "1.0.0",
        "aggregateId": str(ACTIVITY_ID),
        "aggregateVersion": version,
        "correlationId": str(uuid4()),
        "occurredAt": datetime.now(UTC).isoformat(),
        "producer": "event-management",
        "data": {
            "activityId": str(ACTIVITY_ID),
            "eventId": str(UUID("70000000-0000-0000-0000-000000000001")),
            "organizationId": str(ORGANIZATION_ID),
            "status": status,
            "startsAt": (datetime.now(UTC) - timedelta(hours=2)).isoformat(),
            "endsAt": (ends_at or datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
        },
    }


def project_eligibility_facts(
    attendance,
    *,
    status: str = "CONFIRMED",
    activity_ends_at: datetime | None = None,
) -> None:
    repository = attendance.app.state.repository
    repository.apply_event(activity_event(ends_at=activity_ends_at))
    repository.apply_event(
        registration_event(status=status, activity_ends_at=activity_ends_at)
    )


def participant_headers(
    token: str = "participant-token",
    idempotency_key: str = "attendance-test",
) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Idempotency-Key": idempotency_key,
    }
