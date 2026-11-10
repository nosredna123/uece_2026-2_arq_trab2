import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "validate_environment", ROOT / "scripts/validate-environment.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _environment(tmp_path, text):
    path = tmp_path / ".env"
    path.write_text(text, encoding="utf-8")
    return path


def _remove_demo_settings(text):
    return "\n".join(
        line for line in text.splitlines() if not line.startswith("DEMO_WEB_")
    ) + "\n"


def test_backend_only_environment_remains_valid_without_demo_settings(tmp_path):
    example = (ROOT / "infra/.env.example").read_text(encoding="utf-8")
    path = _environment(tmp_path, _remove_demo_settings(example))

    assert MODULE.validate(path) == []


def test_web_enabled_requires_complete_configuration(tmp_path):
    example = (ROOT / "infra/.env.example").read_text(encoding="utf-8")
    incomplete = _remove_demo_settings(example) + "DEMO_WEB_ENABLED=true\n"

    assert MODULE.validate(_environment(tmp_path, incomplete))


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("DEMO_WEB_ENABLED=false", "DEMO_WEB_ENABLED=yes"),
        ("DEMO_WEB_PORT=8085", "DEMO_WEB_PORT=8081"),
        ("DEMO_WEB_PORT=8085", "DEMO_WEB_PORT=65536"),
        ("DEMO_WEB_POLL_TIMEOUT_SECONDS=60", "DEMO_WEB_POLL_TIMEOUT_SECONDS=2"),
        ("DEMO_WEB_FREE_START_DELAY_SECONDS=900",
         "DEMO_WEB_FREE_START_DELAY_SECONDS=240"),
        ("DEMO_WEB_EVENT_BASE_URL=http://event-management:8080",
         "DEMO_WEB_EVENT_BASE_URL=http://attacker.example:8080"),
    ],
)
def test_invalid_web_configuration_is_rejected_without_value_leak(
    tmp_path, old, new
):
    example = (ROOT / "infra/.env.example").read_text(encoding="utf-8")
    assert old in example
    path = _environment(tmp_path, example.replace(old, new))

    errors = MODULE.validate(path)

    assert errors
    assert all("synthetic-" not in error for error in errors)
