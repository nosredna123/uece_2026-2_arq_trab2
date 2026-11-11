import argparse
import json
from pathlib import Path
import sys

from jsonschema import Draft7Validator


ROOT = Path(__file__).resolve().parents[1]


def validate(path: Path) -> list[str]:
    values = {}
    errors = []
    for number, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or key != key.strip() or not key:
            errors.append(f"line {number}: expected KEY=value")
            continue
        if key in values:
            errors.append(f"line {number}: duplicate variable {key}")
        if value.startswith(("'", '"')) or "${" in value:
            errors.append(f"line {number}: quotes/interpolation are unsupported")
        values[key] = value
    schema = json.loads((ROOT / "infra/environment.schema.json").read_text())
    for key in schema["required"]:
        if key not in values:
            errors.append(f"{key}: required variable is missing")
    for error in Draft7Validator(schema).iter_errors(values):
        if error.validator == "required":
            continue
        location = ".".join(str(item) for item in error.path) or "environment"
        errors.append(f"{location}: invalid or missing configuration ({error.validator})")
    if values.get("DEMO_WEB_ENABLED") == "true":
        required_demo_keys = (
            "DEMO_WEB_PORT",
            "DEMO_WEB_SESSION_TTL_SECONDS",
            "DEMO_WEB_MAX_SESSIONS",
            "DEMO_WEB_MAX_COMMANDS_PER_SESSION",
            "DEMO_WEB_HISTORY_LIMIT",
            "DEMO_WEB_POLL_INTERVAL_SECONDS",
            "DEMO_WEB_POLL_TIMEOUT_SECONDS",
            "DEMO_WEB_PAID_START_DELAY_SECONDS",
            "DEMO_WEB_ACTIVITY_DURATION_SECONDS",
            "DEMO_WEB_FREE_START_DELAY_SECONDS",
            "DEMO_WEB_EVENT_BASE_URL",
            "DEMO_WEB_REGISTRATION_BASE_URL",
            "DEMO_WEB_ATTENDANCE_BASE_URL",
            "DEMO_WEB_REPORTING_BASE_URL",
            "DEMO_WEB_IDENTITY_FIXTURES_PATH",
            "DEMO_LAB_ENABLED",
        )
        for key in required_demo_keys:
            if key not in values:
                errors.append(f"{key}: required when DEMO_WEB_ENABLED=true")
    identity_lab_enabled = values.get("IDENTITY_LAB_ENABLED", "false")
    demo_lab_enabled = values.get("DEMO_LAB_ENABLED", "false")
    if identity_lab_enabled != demo_lab_enabled:
        errors.append("DEMO_LAB_ENABLED: must match IDENTITY_LAB_ENABLED")
    if identity_lab_enabled == "true":
        if values.get("DEMO_WEB_ENABLED") != "true":
            errors.append("DEMO_WEB_ENABLED: must be true when the lab is enabled")
        for key in (
            "IDENTITY_LAB_FIXTURES_PATH",
            "DEMO_LAB_FIXTURES_PATH",
            "DEMO_LAB_MAX_PARTICIPANTS",
            "DEMO_LAB_MAX_CONCURRENCY",
            "DEMO_LAB_RUN_TIMEOUT_SECONDS",
            "DEMO_LAB_SETTLE_TIMEOUT_SECONDS",
            "DEMO_LAB_RUN_TTL_SECONDS",
            "DEMO_LAB_MAX_RETAINED_RUNS",
        ):
            if not values.get(key):
                errors.append(f"{key}: required when the lab is enabled")
    lab_bounds = {
        "DEMO_LAB_MAX_PARTICIPANTS": 50,
        "DEMO_LAB_MAX_CONCURRENCY": 10,
        "DEMO_LAB_RUN_TIMEOUT_SECONDS": 60,
        "DEMO_LAB_SETTLE_TIMEOUT_SECONDS": 30,
        "DEMO_LAB_RUN_TTL_SECONDS": 1800,
        "DEMO_LAB_MAX_RETAINED_RUNS": 5,
    }
    for key, maximum_value in lab_bounds.items():
        value = values.get(key, "")
        if value.isdigit() and int(value) > maximum_value:
            errors.append(f"{key}: exceeds the configured safety limit")
    for key, value in values.items():
        if key.endswith("_PORT") and value.isdigit() and int(value) > 65535:
            errors.append(f"{key}: port exceeds 65535")
    initial = values.get("MESSAGE_RETRY_INITIAL_SECONDS", "")
    maximum = values.get("MESSAGE_RETRY_MAX_SECONDS", "")
    if initial.isdigit() and maximum.isdigit() and int(initial) > int(maximum):
        errors.append("MESSAGE_RETRY_MAX_SECONDS: less than initial retry delay")
    poll_interval = values.get("DEMO_WEB_POLL_INTERVAL_SECONDS", "")
    poll_timeout = values.get("DEMO_WEB_POLL_TIMEOUT_SECONDS", "")
    if poll_interval.isdigit() and poll_timeout.isdigit():
        if int(poll_timeout) <= int(poll_interval):
            errors.append("DEMO_WEB_POLL_TIMEOUT_SECONDS: must exceed poll interval")
        if int(poll_timeout) > 300:
            errors.append("DEMO_WEB_POLL_TIMEOUT_SECONDS: exceeds 300")
    paid_start = values.get("DEMO_WEB_PAID_START_DELAY_SECONDS", "")
    activity_duration = values.get("DEMO_WEB_ACTIVITY_DURATION_SECONDS", "")
    free_start = values.get("DEMO_WEB_FREE_START_DELAY_SECONDS", "")
    if paid_start.isdigit() and activity_duration.isdigit() and free_start.isdigit():
        if int(free_start) <= int(paid_start) + int(activity_duration):
            errors.append("DEMO_WEB_FREE_START_DELAY_SECONDS: must follow the paid activity")
    demo_bounds = {
        "DEMO_WEB_SESSION_TTL_SECONDS": 86400,
        "DEMO_WEB_MAX_SESSIONS": 500,
        "DEMO_WEB_MAX_COMMANDS_PER_SESSION": 100,
        "DEMO_WEB_HISTORY_LIMIT": 100,
    }
    for key, maximum_value in demo_bounds.items():
        value = values.get(key, "")
        if value.isdigit() and int(value) > maximum_value:
            errors.append(f"{key}: exceeds the configured safety limit")
    demo_port = values.get("DEMO_WEB_PORT", "")
    if demo_port.isdigit() and not 1024 <= int(demo_port) <= 65535:
        errors.append("DEMO_WEB_PORT: must be between 1024 and 65535")
    host_port_keys = (
        "POSTGRES_PORT",
        "RABBITMQ_HOST_PORT",
        "RABBITMQ_MANAGEMENT_PORT",
        "EVENT_MANAGEMENT_PORT",
        "REGISTRATION_PORT",
        "ATTENDANCE_PORT",
        "REPORTING_PORT",
        "DEMO_WEB_PORT",
    )
    ports = [values[key] for key in host_port_keys if key in values]
    if len(ports) != len(set(ports)):
        errors.append("environment: host ports must be distinct")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate EventHub configuration without printing secrets"
    )
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    errors = validate(args.path)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print("EventHub environment validated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
