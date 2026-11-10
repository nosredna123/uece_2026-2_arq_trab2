from fastapi.testclient import TestClient
import pytest
from pydantic import ValidationError

from reporting_service.app import create_app


def test_missing_database_configuration_fails(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValidationError, match="DATABASE_URL"):
        create_app()


def test_health_and_no_domain_placeholders(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'unit.db'}")
    for key, value in {
        "IDENTITY_BASE_URL": "http://identity-mock:8080",
        "INTERNAL_SERVICE_KEY": "synthetic-unit-only",
        "HTTP_TIMEOUT_SECONDS": "5",
        "MESSAGE_RETRY_INITIAL_SECONDS": "1",
        "MESSAGE_RETRY_MAX_SECONDS": "60",
        "RABBITMQ_HOST": "localhost",
        "RABBITMQ_PORT": "5672",
        "RABBITMQ_USER": "synthetic",
        "RABBITMQ_PASSWORD": "synthetic",
    }.items():
        monkeypatch.setenv(key, value)
    with TestClient(create_app(start_worker=False)) as client:
        assert client.get("/health").json() == {"status": "UP"}
        assert client.get("/organizations/unknown/reports").status_code == 400
