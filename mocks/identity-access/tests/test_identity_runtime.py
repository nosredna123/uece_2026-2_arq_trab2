import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from identity_access_mock.app import create_app
from identity_access_mock.settings import Settings


SERVICE_KEY = "synthetic-internal-service-only"
IDENTITY_URL = "/internal/identity/validate"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("INTERNAL_SERVICE_KEY", SERVICE_KEY)
    monkeypatch.setenv("IDENTITY_MOCK_AVAILABLE", "true")
    monkeypatch.setenv("IDENTITY_MOCK_SCENARIO_POOL_ENABLED", "false")
    with TestClient(create_app()) as test_client:
        yield test_client


def test_health_is_available_without_exposing_identity_configuration(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "UP"}


@pytest.mark.parametrize(
    ("name", "role", "organization_id"),
    [
        ("participant", "PARTICIPANT", None),
        ("participantTwo", "PARTICIPANT", None),
        (
            "organizer",
            "ORGANIZER",
            "30000000-0000-4000-8000-000000000001",
        ),
        ("globalAdmin", "GLOBAL_ADMIN", None),
    ],
)
def test_valid_synthetic_token_returns_trusted_principal_only(client, name, role, organization_id):
    fixtures_path = Path(__file__).resolve().parents[1] / "src/identity_access_mock/fixtures.json"
    token = json.loads(fixtures_path.read_text())[name]["token"]
    response = client.post(
        IDENTITY_URL,
        headers={"X-Service-Key": SERVICE_KEY},
        json={"token": token},
    )

    assert response.status_code == 200
    assert response.json() == {
        "subjectId": {
            "participant": "10000000-0000-4000-8000-000000000001",
            "participantTwo": "10000000-0000-4000-8000-000000000002",
            "organizer": "20000000-0000-4000-8000-000000000001",
            "globalAdmin": "40000000-0000-4000-8000-000000000001",
        }[name],
        "role": role,
        "organizationId": organization_id,
    }
    assert token not in response.text


@pytest.mark.parametrize("service_key", [None, "incorrect-service-key"])
def test_invalid_internal_service_authentication_is_rejected(client, service_key):
    headers = {} if service_key is None else {"X-Service-Key": service_key}
    response = client.post(
        IDENTITY_URL,
        headers=headers,
        json={"token": "synthetic-participant-001"},
    )

    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHORIZED"
    assert "synthetic-internal-service-only" not in response.text


def test_invalid_token_is_rejected_without_anonymous_fallback(client):
    token = "unknown-synthetic-token"
    response = client.post(
        IDENTITY_URL,
        headers={"X-Service-Key": SERVICE_KEY},
        json={"token": token},
    )

    assert response.status_code == 401
    assert response.json()["code"] == "INVALID_IDENTITY"
    assert token not in response.text


def test_caller_cannot_supply_trusted_role_or_organization(client):
    response = client.post(
        IDENTITY_URL,
        headers={"X-Service-Key": SERVICE_KEY},
        json={
            "token": "synthetic-participant-001",
            "role": "GLOBAL_ADMIN",
            "organizationId": "30000000-0000-4000-8000-000000000001",
        },
    )

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_REQUEST"
    assert SERVICE_KEY not in response.text


def test_unavailable_scenario_fails_closed_with_503(monkeypatch):
    monkeypatch.setenv("INTERNAL_SERVICE_KEY", SERVICE_KEY)
    monkeypatch.setenv("IDENTITY_MOCK_AVAILABLE", "false")
    with TestClient(create_app()) as unavailable_client:
        response = unavailable_client.post(
            IDENTITY_URL,
            headers={"X-Service-Key": SERVICE_KEY},
            json={"token": "synthetic-participant-001"},
        )

    assert response.status_code == 503
    assert response.json()["code"] == "IDENTITY_UNAVAILABLE"


def test_identity_settings_are_required_and_secret_is_not_represented(monkeypatch):
    monkeypatch.delenv("INTERNAL_SERVICE_KEY", raising=False)
    with pytest.raises(RuntimeError, match="INTERNAL_SERVICE_KEY"):
        Settings.from_environment()

    monkeypatch.setenv("INTERNAL_SERVICE_KEY", SERVICE_KEY)
    monkeypatch.setenv("IDENTITY_MOCK_AVAILABLE", "yes")
    with pytest.raises(RuntimeError, match="IDENTITY_MOCK_AVAILABLE"):
        Settings.from_environment()

    monkeypatch.setenv("IDENTITY_MOCK_AVAILABLE", "true")
    assert SERVICE_KEY not in repr(Settings.from_environment())


def test_scenario_pool_settings_require_explicit_boolean_and_path(monkeypatch):
    monkeypatch.setenv("INTERNAL_SERVICE_KEY", SERVICE_KEY)
    monkeypatch.setenv("IDENTITY_MOCK_AVAILABLE", "true")
    monkeypatch.setenv("IDENTITY_MOCK_SCENARIO_POOL_ENABLED", "yes")
    with pytest.raises(RuntimeError, match="IDENTITY_MOCK_SCENARIO_POOL_ENABLED"):
        Settings.from_environment()

    monkeypatch.setenv("IDENTITY_MOCK_SCENARIO_POOL_ENABLED", "true")
    monkeypatch.delenv("IDENTITY_MOCK_SCENARIO_FIXTURES_PATH", raising=False)
    with pytest.raises(RuntimeError, match="IDENTITY_MOCK_SCENARIO_FIXTURES_PATH"):
        Settings.from_environment()


def test_every_response_has_a_correlation_id_and_invalid_ids_are_rejected(client, caplog):
    correlation_id = "90000000-0000-0000-0000-000000000001"
    valid = client.post(
        IDENTITY_URL,
        headers={
            "X-Service-Key": SERVICE_KEY,
            "X-Correlation-ID": correlation_id,
        },
        json={"token": "synthetic-participant-001"},
    )
    assert valid.headers["X-Correlation-ID"] == correlation_id
    request_log = next(
        record
        for record in caplog.records
        if record.name == "identity_access_mock.app"
        and record.getMessage() == "http.request.completed"
    )
    assert request_log.correlation_id == correlation_id
    assert request_log.status_code == 200

    invalid = client.post(
        IDENTITY_URL,
        headers={"X-Service-Key": SERVICE_KEY, "X-Correlation-ID": "not-a-uuid"},
        json={"token": "synthetic-participant-001"},
    )
    assert invalid.status_code == 400
    assert invalid.json()["code"] == "INVALID_CORRELATION_ID"
    assert invalid.headers["X-Correlation-ID"] == invalid.json()["correlationId"]


def test_empty_correlation_id_is_rejected(client):
    response = client.get("/health", headers={"X-Correlation-ID": ""})
    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_CORRELATION_ID"
    assert response.headers["X-Correlation-ID"] == response.json()["correlationId"]
