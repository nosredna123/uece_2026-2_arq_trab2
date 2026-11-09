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
    for key, value in values.items():
        if key.endswith("_PORT") and value.isdigit() and int(value) > 65535:
            errors.append(f"{key}: port exceeds 65535")
    initial = values.get("MESSAGE_RETRY_INITIAL_SECONDS", "")
    maximum = values.get("MESSAGE_RETRY_MAX_SECONDS", "")
    if initial.isdigit() and maximum.isdigit() and int(initial) > int(maximum):
        errors.append("MESSAGE_RETRY_MAX_SECONDS: less than initial retry delay")
    host_port_keys = (
        "POSTGRES_PORT", "RABBITMQ_PORT", "RABBITMQ_MANAGEMENT_PORT",
        "EVENT_MANAGEMENT_PORT", "REGISTRATION_PORT", "ATTENDANCE_PORT",
        "REPORTING_PORT", "IDENTITY_MOCK_PORT",
    )
    ports = [values[key] for key in host_port_keys if key in values]
    if len(ports) != len(set(ports)):
        errors.append("environment: host ports must be distinct")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate EventHub configuration without printing secrets")
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
