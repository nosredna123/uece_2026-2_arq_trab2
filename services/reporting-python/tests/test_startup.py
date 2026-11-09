from fastapi.testclient import TestClient
import pytest

from reporting_service.app import create_app


def test_missing_database_configuration_fails(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(KeyError, match="DATABASE_URL"):
        create_app()


def test_health_and_no_domain_placeholders(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'unit.db'}")
    with TestClient(create_app()) as client:
        assert client.get("/health").json() == {"status": "UP"}
        assert client.get("/organizations/unknown/reports").status_code == 404
