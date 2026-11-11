import json

import pytest

from demo_web.settings import Settings, SettingsError


def test_loads_explicit_configuration_and_fixture_roles(web_environment):
    settings = Settings.from_environment(web_environment)

    assert settings.enabled is True
    assert settings.port == 8085
    assert settings.http_timeout_seconds == 5
    assert settings.event_base_url == "http://event-management:8080"
    assert settings.identity_fixtures_path.is_file()
    assert settings.max_sessions == 100


def test_requires_every_setting_and_explicit_enabled_true(web_environment):
    missing = dict(web_environment)
    del missing["DEMO_WEB_HISTORY_LIMIT"]
    with pytest.raises(SettingsError, match="DEMO_WEB_HISTORY_LIMIT"):
        Settings.from_environment(missing)

    disabled = {**web_environment, "DEMO_WEB_ENABLED": "false"}
    with pytest.raises(SettingsError, match="DEMO_WEB_ENABLED"):
        Settings.from_environment(disabled)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("DEMO_WEB_SESSION_TTL_SECONDS", "0"),
        ("DEMO_WEB_MAX_SESSIONS", "501"),
        ("DEMO_WEB_MAX_COMMANDS_PER_SESSION", "101"),
        ("DEMO_WEB_HISTORY_LIMIT", "101"),
        ("DEMO_WEB_POLL_INTERVAL_SECONDS", "60"),
        ("DEMO_WEB_POLL_TIMEOUT_SECONDS", "2"),
        ("DEMO_WEB_PAID_START_DELAY_SECONDS", "0"),
        ("DEMO_WEB_ACTIVITY_DURATION_SECONDS", "0"),
        ("DEMO_WEB_FREE_START_DELAY_SECONDS", "240"),
        ("DEMO_WEB_PORT", "70000"),
    ],
)
def test_rejects_invalid_bounds_and_schedule(web_environment, key, value):
    with pytest.raises(SettingsError):
        Settings.from_environment({**web_environment, key: value})


@pytest.mark.parametrize(
    "url",
    [
        "https://event-management:8080",
        "http://attacker.example:8080",
        "http://event-management:8080/private",
        "http://user:password@event-management:8080",
    ],
)
def test_rejects_non_internal_or_non_origin_service_urls(web_environment, url):
    configured = {**web_environment, "DEMO_WEB_EVENT_BASE_URL": url}
    with pytest.raises(SettingsError, match="DEMO_WEB_EVENT_BASE_URL"):
        Settings.from_environment(configured)


def test_rejects_port_collisions_with_existing_stack(web_environment):
    configured = {**web_environment, "EVENT_MANAGEMENT_PORT": "8085"}
    with pytest.raises(SettingsError, match="DEMO_WEB_PORT"):
        Settings.from_environment(configured)


@pytest.mark.parametrize(
    "fixture",
    [
        {"participant": {"token": "token", "subjectId": "id", "role": "VISITOR"}},
        {"organizer": {"token": "token", "subjectId": "id", "role": "ORGANIZER"}},
        {"participant": {"token": "token", "subjectId": "id", "role": "PARTICIPANT"}},
    ],
)
def test_rejects_invalid_identity_fixture_roles_or_shape(
    web_environment, identity_fixtures, fixture
):
    identity_fixtures.write_text(json.dumps(fixture), encoding="utf-8")
    with pytest.raises(SettingsError, match="fixture"):
        Settings.from_environment(web_environment)


def test_rejects_unreadable_fixture_path(web_environment, tmp_path):
    configured = {
        **web_environment,
        "DEMO_WEB_IDENTITY_FIXTURES_PATH": str(tmp_path / "missing.json"),
    }
    with pytest.raises(SettingsError, match="DEMO_WEB_IDENTITY_FIXTURES_PATH"):
        Settings.from_environment(configured)


def test_rejects_organizer_fixtures_that_share_an_organization(
    web_environment, identity_fixtures
):
    fixtures = json.loads(identity_fixtures.read_text(encoding="utf-8"))
    fixtures["organizerUece"]["organizationId"] = fixtures["organizer"]["organizationId"]
    identity_fixtures.write_text(json.dumps(fixtures), encoding="utf-8")

    with pytest.raises(SettingsError, match="organizations must be unique"):
        Settings.from_environment(web_environment)


def test_configuration_errors_do_not_echo_secret_values(web_environment):
    secret = "do-not-print-this-value"
    configured = {
        **web_environment,
        "DEMO_WEB_EVENT_BASE_URL": f"http://{secret}@event-management:8080",
    }
    with pytest.raises(SettingsError) as error:
        Settings.from_environment(configured)
    assert secret not in str(error.value)
