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
    ("REPORTING_PORT=8084", "REPORTING_PORT=8081"),
])
def test_invalid_configuration_fails_without_exposing_values(tmp_path, old, new):
    example = (ROOT / "infra/.env.example").read_text()
    path = tmp_path / ".env"
    path.write_text(example.replace(old, new))
    errors = MODULE.validate(path)
    assert errors
    assert all("synthetic-" not in error for error in errors)
