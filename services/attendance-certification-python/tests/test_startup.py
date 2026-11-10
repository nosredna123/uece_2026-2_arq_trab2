import pytest
from pydantic import ValidationError

from attendance_service.app import create_app


def test_missing_database_configuration_fails(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValidationError, match="DATABASE_URL"):
        create_app()


def test_health_uses_the_service_database(attendance):
    response = attendance.client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "UP"}
