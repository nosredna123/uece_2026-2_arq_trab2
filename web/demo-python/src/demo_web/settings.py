import json
import os
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit


class SettingsError(ValueError):
    """Raised when required presentation configuration is missing or invalid."""


_REQUIRED = (
    "HTTP_TIMEOUT_SECONDS",
    "DEMO_WEB_ENABLED",
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
)
_SERVICE_URLS = {
    "DEMO_WEB_EVENT_BASE_URL": "event-management",
    "DEMO_WEB_REGISTRATION_BASE_URL": "registration",
    "DEMO_WEB_ATTENDANCE_BASE_URL": "attendance",
    "DEMO_WEB_REPORTING_BASE_URL": "reporting",
}
_FIXTURE_ROLES = {
    "participant": "PARTICIPANT",
    "participantTwo": "PARTICIPANT",
    "organizer": "ORGANIZER",
    "globalAdmin": "GLOBAL_ADMIN",
}


def _positive_integer(values: Mapping[str, str], key: str) -> int:
    value = values[key]
    if not value.isascii() or not value.isdecimal() or int(value) <= 0:
        raise SettingsError(f"{key} must be a positive integer.")
    return int(value)


def _validate_fixtures(path: Path) -> None:
    try:
        fixtures = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise SettingsError(
            "DEMO_WEB_IDENTITY_FIXTURES_PATH must reference a readable JSON fixture."
        ) from None
    if not isinstance(fixtures, dict) or set(fixtures) != set(_FIXTURE_ROLES):
        raise SettingsError("Identity fixture roles or names are invalid.")
    for name, role in _FIXTURE_ROLES.items():
        fixture = fixtures[name]
        if (
            not isinstance(fixture, dict)
            or fixture.get("role") != role
            or not isinstance(fixture.get("token"), str)
            or not fixture["token"]
            or not isinstance(fixture.get("subjectId"), str)
            or not fixture["subjectId"]
        ):
            raise SettingsError("Identity fixture roles or entries are invalid.")
        if role == "ORGANIZER" and not fixture.get("organizationId"):
            raise SettingsError("Organizer identity fixture requires an organization.")


class Settings:
    enabled: bool
    http_timeout_seconds: int
    port: int
    session_ttl_seconds: int
    max_sessions: int
    max_commands_per_session: int
    history_limit: int
    poll_interval_seconds: int
    poll_timeout_seconds: int
    paid_start_delay_seconds: int
    activity_duration_seconds: int
    free_start_delay_seconds: int
    event_base_url: str
    registration_base_url: str
    attendance_base_url: str
    reporting_base_url: str
    identity_fixtures_path: Path

    def __init__(self, values: Mapping[str, str]):
        missing = [key for key in _REQUIRED if key not in values or not values[key]]
        if missing:
            raise SettingsError(f"Missing required settings: {', '.join(missing)}.")
        if values["DEMO_WEB_ENABLED"] != "true":
            raise SettingsError("DEMO_WEB_ENABLED must be explicitly set to true.")

        self.enabled = True
        self.http_timeout_seconds = _positive_integer(values, "HTTP_TIMEOUT_SECONDS")
        self.port = _positive_integer(values, "DEMO_WEB_PORT")
        if self.port < 1024 or self.port > 65535:
            raise SettingsError("DEMO_WEB_PORT must be between 1024 and 65535.")

        self.session_ttl_seconds = _positive_integer(
            values, "DEMO_WEB_SESSION_TTL_SECONDS"
        )
        self.max_sessions = _positive_integer(values, "DEMO_WEB_MAX_SESSIONS")
        self.max_commands_per_session = _positive_integer(
            values, "DEMO_WEB_MAX_COMMANDS_PER_SESSION"
        )
        self.history_limit = _positive_integer(values, "DEMO_WEB_HISTORY_LIMIT")
        if self.session_ttl_seconds > 86400:
            raise SettingsError("DEMO_WEB_SESSION_TTL_SECONDS exceeds 86400.")
        if self.max_sessions > 500:
            raise SettingsError("DEMO_WEB_MAX_SESSIONS exceeds 500.")
        if self.max_commands_per_session > 100:
            raise SettingsError("DEMO_WEB_MAX_COMMANDS_PER_SESSION exceeds 100.")
        if self.history_limit > 100:
            raise SettingsError("DEMO_WEB_HISTORY_LIMIT exceeds 100.")

        self.poll_interval_seconds = _positive_integer(
            values, "DEMO_WEB_POLL_INTERVAL_SECONDS"
        )
        self.poll_timeout_seconds = _positive_integer(
            values, "DEMO_WEB_POLL_TIMEOUT_SECONDS"
        )
        if self.poll_timeout_seconds <= self.poll_interval_seconds:
            raise SettingsError(
                "DEMO_WEB_POLL_TIMEOUT_SECONDS must exceed the poll interval."
            )
        if self.poll_timeout_seconds > 300:
            raise SettingsError("DEMO_WEB_POLL_TIMEOUT_SECONDS exceeds 300.")

        self.paid_start_delay_seconds = _positive_integer(
            values, "DEMO_WEB_PAID_START_DELAY_SECONDS"
        )
        self.activity_duration_seconds = _positive_integer(
            values, "DEMO_WEB_ACTIVITY_DURATION_SECONDS"
        )
        self.free_start_delay_seconds = _positive_integer(
            values, "DEMO_WEB_FREE_START_DELAY_SECONDS"
        )
        if (
            self.free_start_delay_seconds
            <= self.paid_start_delay_seconds + self.activity_duration_seconds
        ):
            raise SettingsError(
                "DEMO_WEB_FREE_START_DELAY_SECONDS must follow the paid activity."
            )

        for key, hostname in _SERVICE_URLS.items():
            self._validate_service_origin(key, values[key], hostname)
        self.event_base_url = values["DEMO_WEB_EVENT_BASE_URL"]
        self.registration_base_url = values["DEMO_WEB_REGISTRATION_BASE_URL"]
        self.attendance_base_url = values["DEMO_WEB_ATTENDANCE_BASE_URL"]
        self.reporting_base_url = values["DEMO_WEB_REPORTING_BASE_URL"]

        self.identity_fixtures_path = Path(values["DEMO_WEB_IDENTITY_FIXTURES_PATH"])
        _validate_fixtures(self.identity_fixtures_path)
        self._validate_host_port_collisions(values)

    @staticmethod
    def _validate_service_origin(key: str, value: str, hostname: str) -> None:
        try:
            parsed = urlsplit(value)
            valid = (
                parsed.scheme == "http"
                and parsed.hostname == hostname
                and parsed.port == 8080
                and parsed.path in ("", "/")
                and not parsed.username
                and not parsed.password
                and not parsed.query
                and not parsed.fragment
            )
        except ValueError:
            valid = False
        if not valid:
            raise SettingsError(f"{key} must be the fixed internal service origin.")

    def _validate_host_port_collisions(self, values: Mapping[str, str]) -> None:
        for key in (
            "POSTGRES_PORT",
            "RABBITMQ_HOST_PORT",
            "RABBITMQ_MANAGEMENT_PORT",
            "EVENT_MANAGEMENT_PORT",
            "REGISTRATION_PORT",
            "ATTENDANCE_PORT",
            "REPORTING_PORT",
        ):
            if values.get(key) == str(self.port):
                raise SettingsError(f"DEMO_WEB_PORT conflicts with {key}.")

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> "Settings":
        return cls(os.environ if environ is None else environ)
