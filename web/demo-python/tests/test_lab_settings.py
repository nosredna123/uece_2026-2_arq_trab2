import json

from fastapi.testclient import TestClient
import pytest

from demo_web.app import create_app
from demo_web.settings import Settings, SettingsError


def _scenario_data(count=20):
    return {
        f"scenarioParticipant{index:02}": {
            "token": f"private-scenario-token-{index:02}",
            "subjectId": f"50000000-0000-4000-8000-{index:012d}",
            "role": "PARTICIPANT",
        }
        for index in range(1, count + 1)
    }


def _enabled_lab(web_environment, path):
    return {
        **web_environment,
        "IDENTITY_LAB_ENABLED": "true",
        "DEMO_LAB_ENABLED": "true",
        "DEMO_LAB_FIXTURES_PATH": str(path),
        "DEMO_LAB_MAX_PARTICIPANTS": "50",
        "DEMO_LAB_MAX_CONCURRENCY": "10",
        "DEMO_LAB_RUN_TIMEOUT_SECONDS": "60",
        "DEMO_LAB_SETTLE_TIMEOUT_SECONDS": "30",
        "DEMO_LAB_RUN_TTL_SECONDS": "1800",
        "DEMO_LAB_MAX_RETAINED_RUNS": "5",
    }


def test_scenario_pool_is_disabled_by_default(web_environment):
    settings = Settings.from_environment(web_environment)

    assert settings.lab_enabled is False
    assert settings.lab_fixtures_path is None


def test_enabled_pool_requires_and_validates_exactly_twenty_participants(web_environment, tmp_path):
    path = tmp_path / "scenario-participants.json"
    path.write_text(json.dumps(_scenario_data()), encoding="utf-8")
    configured = _enabled_lab(web_environment, path)

    settings = Settings.from_environment(configured)

    assert settings.lab_enabled is True
    assert settings.lab_fixtures_path == path
    assert settings.lab_max_participants == 50
    assert settings.lab_max_concurrency == 10


def test_enabled_pool_requires_scenario_fixture_path(web_environment):
    configured = {
        **web_environment,
        "IDENTITY_LAB_ENABLED": "true",
        "DEMO_LAB_ENABLED": "true",
    }

    with pytest.raises(SettingsError, match="DEMO_LAB_FIXTURES_PATH"):
        Settings.from_environment(configured)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda data: data.pop("scenarioParticipant20"),
        lambda data: data.update(
            scenarioParticipant21={
                "token": "private-scenario-token-21",
                "subjectId": "50000000-0000-4000-8000-000000000021",
                "role": "PARTICIPANT",
            }
        ),
        lambda data: data["scenarioParticipant01"].update(role="GLOBAL_ADMIN"),
        lambda data: data["scenarioParticipant01"].update(
            organizationId="30000000-0000-4000-8000-000000000001"
        ),
        lambda data: data["scenarioParticipant01"].update(subjectId="bad-id"),
        lambda data: data["scenarioParticipant02"].update(
            token=data["scenarioParticipant01"]["token"]
        ),
        lambda data: data["scenarioParticipant02"].update(
            subjectId=data["scenarioParticipant01"]["subjectId"]
        ),
        lambda data: data["scenarioParticipant01"].update(extra="not-allowed"),
        lambda data: data["scenarioParticipant01"].update(token="t" * 4097),
    ],
)
def test_rejects_malformed_oversized_or_privileged_pool(web_environment, tmp_path, mutate):
    data = _scenario_data()
    mutate(data)
    path = tmp_path / "scenario-participants.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    configured = _enabled_lab(web_environment, path)

    with pytest.raises(SettingsError):
        Settings.from_environment(configured)


def test_rejects_pool_credential_collisions_with_baseline(
    web_environment, identity_fixtures, tmp_path
):
    data = _scenario_data()
    data["scenarioParticipant01"]["token"] = "synthetic-participant"
    path = tmp_path / "scenario-participants.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    configured = _enabled_lab(web_environment, path)

    with pytest.raises(SettingsError):
        Settings.from_environment(configured)


def test_rejects_pool_subject_collisions_with_baseline(web_environment, tmp_path):
    data = _scenario_data()
    data["scenarioParticipant01"]["subjectId"] = "10000000-0000-4000-8000-000000000001"
    path = tmp_path / "scenario-participants.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    configured = _enabled_lab(web_environment, path)

    with pytest.raises(SettingsError):
        Settings.from_environment(configured)


def test_rejects_pool_configuration_when_mock_and_web_are_mismatched(web_environment, tmp_path):
    path = tmp_path / "scenario-participants.json"
    path.write_text(json.dumps(_scenario_data()), encoding="utf-8")
    configured = {
        **_enabled_lab(web_environment, path),
        "DEMO_LAB_ENABLED": "false",
    }

    with pytest.raises(SettingsError):
        Settings.from_environment(configured)


def test_scenario_credentials_are_not_rendered_in_browser_responses(web_environment, tmp_path):
    scenarios = _scenario_data()
    path = tmp_path / "scenario-participants.json"
    path.write_text(json.dumps(scenarios), encoding="utf-8")
    configured = _enabled_lab(web_environment, path)

    with TestClient(create_app(configured)) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert all(identity["token"] not in response.text for identity in scenarios.values())


def test_rejects_oversized_pool_file(web_environment, tmp_path):
    path = tmp_path / "scenario-participants.json"
    path.write_text(" " * 40000, encoding="utf-8")
    configured = _enabled_lab(web_environment, path)

    with pytest.raises(SettingsError):
        Settings.from_environment(configured)
