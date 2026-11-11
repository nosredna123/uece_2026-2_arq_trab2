import json
import os
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit
from uuid import UUID


SCENARIO_PARTICIPANT_COUNT = 20
MAX_SCENARIO_FIXTURE_BYTES = 32_768


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
    "IDENTITY_LAB_ENABLED",
    "DEMO_LAB_ENABLED",
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
    "organizerUece": "ORGANIZER",
    "organizerAcademicSim": "ORGANIZER",
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
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise SettingsError(
            "DEMO_WEB_IDENTITY_FIXTURES_PATH must reference a readable JSON fixture."
        ) from None
    if not isinstance(fixtures, dict) or set(fixtures) != set(_FIXTURE_ROLES):
        raise SettingsError("Identity fixture roles or names are invalid.")
    tokens = []
    subjects = []
    organizations = []
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
        tokens.append(fixture["token"])
        subjects.append(fixture["subjectId"])
        if role == "ORGANIZER":
            organization_id = fixture.get("organizationId")
            try:
                UUID(organization_id)
            except (TypeError, ValueError):
                raise SettingsError(
                    "Organizer identity fixture requires a valid organization."
                ) from None
            organizations.append(organization_id)
    if (
        len(tokens) != len(set(tokens))
        or len(subjects) != len(set(subjects))
        or len(organizations) != len(set(organizations))
    ):
        raise SettingsError("Identity fixture tokens, subjects, and organizations must be unique.")


def _validate_scenario_fixtures(path: Path, baseline_path: Path) -> None:
    try:
        if path.stat().st_size > MAX_SCENARIO_FIXTURE_BYTES:
            raise SettingsError("Scenario identity fixture exceeds its size limit.")
        fixtures = json.loads(path.read_text(encoding="utf-8"))
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise SettingsError(
            "DEMO_LAB_FIXTURES_PATH must reference a readable JSON fixture."
        ) from None
    expected_names = {
        f"scenarioParticipant{index:02}" for index in range(1, SCENARIO_PARTICIPANT_COUNT + 1)
    }
    if not isinstance(fixtures, dict) or set(fixtures) != expected_names:
        raise SettingsError("Scenario identity fixture must contain twenty actors.")

    tokens = []
    subjects = []
    for fixture in (*baseline.values(), *fixtures.values()):
        if not isinstance(fixture, dict):
            raise SettingsError("Identity fixture roles or entries are invalid.")
        token = fixture.get("token")
        subject_id = fixture.get("subjectId")
        if not isinstance(token, str) or not token or len(token) > 4096:
            raise SettingsError("Identity fixture roles or entries are invalid.")
        if not isinstance(subject_id, str):
            raise SettingsError("Identity fixture roles or entries are invalid.")
        try:
            UUID(subject_id)
        except ValueError:
            raise SettingsError("Identity fixture roles or entries are invalid.") from None
        tokens.append(token)
        subjects.append(subject_id)

    for fixture in fixtures.values():
        if (
            not isinstance(fixture, dict)
            or set(fixture) != {"token", "subjectId", "role"}
            or fixture.get("role") != "PARTICIPANT"
        ):
            raise SettingsError("Scenario identities must be unscoped participants.")
    if len(tokens) != len(set(tokens)) or len(subjects) != len(set(subjects)):
        raise SettingsError("Identity fixture tokens and subjects must be unique.")


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
    lab_enabled: bool
    lab_fixtures_path: Path | None
    lab_max_participants: int | None
    lab_max_concurrency: int | None
    lab_run_timeout_seconds: int | None
    lab_settle_timeout_seconds: int | None
    lab_run_ttl_seconds: int | None
    lab_max_retained_runs: int | None
    seed_data_path: Path | None

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

        self.session_ttl_seconds = _positive_integer(values, "DEMO_WEB_SESSION_TTL_SECONDS")
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

        self.poll_interval_seconds = _positive_integer(values, "DEMO_WEB_POLL_INTERVAL_SECONDS")
        self.poll_timeout_seconds = _positive_integer(values, "DEMO_WEB_POLL_TIMEOUT_SECONDS")
        if self.poll_timeout_seconds <= self.poll_interval_seconds:
            raise SettingsError("DEMO_WEB_POLL_TIMEOUT_SECONDS must exceed the poll interval.")
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
            raise SettingsError("DEMO_WEB_FREE_START_DELAY_SECONDS must follow the paid activity.")

        for key, hostname in _SERVICE_URLS.items():
            self._validate_service_origin(key, values[key], hostname)
        self.event_base_url = values["DEMO_WEB_EVENT_BASE_URL"]
        self.registration_base_url = values["DEMO_WEB_REGISTRATION_BASE_URL"]
        self.attendance_base_url = values["DEMO_WEB_ATTENDANCE_BASE_URL"]
        self.reporting_base_url = values["DEMO_WEB_REPORTING_BASE_URL"]

        self.identity_fixtures_path = Path(values["DEMO_WEB_IDENTITY_FIXTURES_PATH"])
        _validate_fixtures(self.identity_fixtures_path)
        identity_lab_enabled = values["IDENTITY_LAB_ENABLED"]
        demo_lab_enabled = values["DEMO_LAB_ENABLED"]
        if identity_lab_enabled not in {"true", "false"} or demo_lab_enabled not in {
            "true",
            "false",
        }:
            raise SettingsError("Lab settings must be exactly true or false.")
        if identity_lab_enabled != demo_lab_enabled:
            raise SettingsError("Lab settings must match the identity mock.")
        self.lab_enabled = demo_lab_enabled == "true"
        lab_path_value = values.get("DEMO_LAB_FIXTURES_PATH", "")
        self.lab_fixtures_path = None
        lab_limit_keys = {
            "DEMO_LAB_MAX_PARTICIPANTS": 50,
            "DEMO_LAB_MAX_CONCURRENCY": 10,
            "DEMO_LAB_RUN_TIMEOUT_SECONDS": 60,
            "DEMO_LAB_SETTLE_TIMEOUT_SECONDS": 30,
            "DEMO_LAB_RUN_TTL_SECONDS": 1800,
            "DEMO_LAB_MAX_RETAINED_RUNS": 5,
        }
        lab_limits: dict[str, int] = {}
        self.lab_max_participants = None
        self.lab_max_concurrency = None
        self.lab_run_timeout_seconds = None
        self.lab_settle_timeout_seconds = None
        self.lab_run_ttl_seconds = None
        self.lab_max_retained_runs = None
        if self.lab_enabled:
            if not lab_path_value:
                raise SettingsError("DEMO_LAB_FIXTURES_PATH is required when the lab is enabled.")
            self.lab_fixtures_path = Path(lab_path_value)
            _validate_scenario_fixtures(
                self.lab_fixtures_path,
                self.identity_fixtures_path,
            )
            for key, maximum in lab_limit_keys.items():
                if key not in values or not values[key]:
                    raise SettingsError(f"{key} is required when the lab is enabled.")
                lab_limits[key] = _positive_integer(values, key)
                if lab_limits[key] > maximum:
                    raise SettingsError(f"{key} exceeds the safety limit of {maximum}.")
            if (
                lab_limits["DEMO_LAB_SETTLE_TIMEOUT_SECONDS"]
                > lab_limits["DEMO_LAB_RUN_TIMEOUT_SECONDS"]
            ):
                raise SettingsError(
                    "DEMO_LAB_SETTLE_TIMEOUT_SECONDS must not exceed the run timeout."
                )
            if (
                lab_limits["DEMO_LAB_RUN_TTL_SECONDS"]
                < lab_limits["DEMO_LAB_RUN_TIMEOUT_SECONDS"]
                + lab_limits["DEMO_LAB_SETTLE_TIMEOUT_SECONDS"]
            ):
                raise SettingsError("DEMO_LAB_RUN_TTL_SECONDS must cover run and settle timeouts.")
            (
                self.lab_max_participants,
                self.lab_max_concurrency,
                self.lab_run_timeout_seconds,
                self.lab_settle_timeout_seconds,
                self.lab_run_ttl_seconds,
                self.lab_max_retained_runs,
            ) = tuple(lab_limits.values())
        elif lab_path_value or any(key in values for key in lab_limit_keys):
            raise SettingsError(
                "Lab fixtures and limits must only be configured when DEMO_LAB_ENABLED=true."
            )
        seed_path_value = values.get("DEMO_WEB_SEED_DATA_PATH", "")
        self.seed_data_path = Path(seed_path_value) if seed_path_value else None
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
