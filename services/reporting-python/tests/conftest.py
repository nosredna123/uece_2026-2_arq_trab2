from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi.testclient import TestClient


ORGANIZATION_ID = UUID("20000000-0000-0000-0000-000000000001")
OTHER_ORGANIZATION_ID = UUID("20000000-0000-0000-0000-000000000002")
ACTIVITY_ID = UUID("40000000-0000-0000-0000-000000000001")
OTHER_ACTIVITY_ID = UUID("40000000-0000-0000-0000-000000000002")
PARTICIPANT_ID = UUID("30000000-0000-0000-0000-000000000001")
REGISTRATION_ID = UUID("50000000-0000-0000-0000-000000000001")


class FakeIdentityClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.principals = {
            "organizer-token": {
                "subject_id": UUID("60000000-0000-0000-0000-000000000001"),
                "role": "ORGANIZER",
                "organization_id": ORGANIZATION_ID,
            },
            "other-organizer-token": {
                "subject_id": UUID("60000000-0000-0000-0000-000000000002"),
                "role": "ORGANIZER",
                "organization_id": OTHER_ORGANIZATION_ID,
            },
            "admin-token": {
                "subject_id": UUID("60000000-0000-0000-0000-000000000003"),
                "role": "GLOBAL_ADMIN",
                "organization_id": None,
            },
            "participant-token": {
                "subject_id": PARTICIPANT_ID,
                "role": "PARTICIPANT",
                "organization_id": None,
            },
        }

    def validate(self, token: str, correlation_id: str) -> dict | None:
        self.calls.append((token, correlation_id))
        return self.principals.get(token)


@pytest.fixture
def reporting(monkeypatch, tmp_path):
    values = {
        "DATABASE_URL": f"sqlite:///{tmp_path / 'reporting.db'}",
        "IDENTITY_BASE_URL": "http://identity-mock:8080",
        "INTERNAL_SERVICE_KEY": "synthetic-unit-only",
        "HTTP_TIMEOUT_SECONDS": "5",
        "MESSAGE_RETRY_INITIAL_SECONDS": "1",
        "MESSAGE_RETRY_MAX_SECONDS": "60",
        "RABBITMQ_HOST": "localhost",
        "RABBITMQ_PORT": "5672",
        "RABBITMQ_USER": "synthetic",
        "RABBITMQ_PASSWORD": "synthetic",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)

    from reporting_service.app import create_app
    from reporting_service.database import metadata

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
