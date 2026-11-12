import importlib.util
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("validate_environment", ROOT / "scripts/validate-environment.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_example_is_valid_and_real_env_is_ignored():
    assert MODULE.validate(ROOT / "infra/.env.example") == []
    result = subprocess.run(
        ["git", "check-ignore", "infra/.env"], cwd=ROOT, capture_output=True,
    )
    assert result.returncode == 0


@pytest.mark.parametrize("old,new", [
    ("RESERVATION_EXPIRY_MINUTES=30", "RESERVATION_EXPIRY_MINUTES=0"),
    ("WAITLIST_ENABLED=true", "WAITLIST_ENABLED=yes"),
    ("CERTIFICATE_ISSUANCE_AFTER_EVENT=true", "CERTIFICATE_ISSUANCE_AFTER_EVENT=false"),
    ("REFUND_PERCENTAGE=90", "REFUND_PERCENTAGE=101"),
    ("HTTP_TIMEOUT_SECONDS=5", ""),
    ("EVENT_MANAGEMENT_PORT=8081", "EVENT_MANAGEMENT_PORT=99999"),
    ("MESSAGE_RETRY_MAX_SECONDS=60", "MESSAGE_RETRY_MAX_SECONDS=0"),
    ("IDENTITY_MOCK_AVAILABLE=true", "IDENTITY_MOCK_AVAILABLE=yes"),
    ("BILLING_STORE_PATH=/data/billing.sqlite3", "BILLING_STORE_PATH="),
    ("NOTIFICATION_STORE_PATH=/data/notification.sqlite3", "NOTIFICATION_STORE_PATH="),
    ("REPORTING_PORT=8084", "REPORTING_PORT=8081"),
    ("RABBITMQ_HOST_PORT=5672", "RABBITMQ_HOST_PORT=8081"),
    ("RABBITMQ_HOST=rabbitmq", "RABBITMQ_HOST=localhost"),
    ("IDENTITY_BASE_URL=http://identity-mock:8080", "IDENTITY_BASE_URL=http://localhost:8085"),
])
def test_invalid_configuration_fails_without_exposing_values(tmp_path, old, new):
    example = (ROOT / "infra/.env.example").read_text()
    path = tmp_path / ".env"
    path.write_text(example.replace(old, new))
    errors = MODULE.validate(path)
    assert errors
    assert all("synthetic-" not in error for error in errors)


@pytest.mark.parametrize("value,valid", [
    ("100", True), ("1000", True), ("30000", True),
    ("99", False), ("30001", False), ("0", False), ("1s", False),
])
def test_registration_outbox_interval_is_bounded(tmp_path, value, valid):
    path = tmp_path / ".env"
    example = (ROOT / "infra/.env.example").read_text()
    path.write_text(f"{example}\nREGISTRATION_OUTBOX_PUBLISH_INTERVAL_MS={value}\n")
    assert (MODULE.validate(path) == []) is valid


def test_registration_receives_outbox_interval_and_launcher_speeds_it_up():
    compose = (ROOT / "infra/docker-compose.yml").read_text()
    assert "OUTBOX_PUBLISH_INTERVAL_MS: ${REGISTRATION_OUTBOX_PUBLISH_INTERVAL_MS:-30000}" in compose
    launcher = (ROOT / "scripts/run-demo-web.sh").read_text()
    assert 'REGISTRATION_OUTBOX_PUBLISH_INTERVAL_MS="${outbox_interval:-1000}"' in launcher
