import json
from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient
import pytest

from identity_access_mock.app import (
    MAX_SCENARIO_FIXTURE_BYTES,
    SCENARIO_PARTICIPANT_COUNT,
    create_app,
    load_fixtures,
    load_scenario_fixtures,
)


SERVICE_KEY = "synthetic-internal-service-only"
IDENTITY_URL = "/internal/identity/validate"
FIXTURE_PATH = Path(__file__).resolve().parents[1] / "scenario-participants.json"


def _scenario_data(count=SCENARIO_PARTICIPANT_COUNT):
    return {
        f"scenarioParticipant{index:02}": {
            "token": f"synthetic-scenario-participant-{index:02}",
            "subjectId": f"50000000-0000-4000-8000-{index:012d}",
            "role": "PARTICIPANT",
        }
        for index in range(1, count + 1)
    }


def _write_scenario(tmp_path, data):
    path = tmp_path / "scenario-participants.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_default_behavior_remains_limited_to_the_six_baseline_fixtures(
    monkeypatch,
):
    monkeypatch.setenv("INTERNAL_SERVICE_KEY", SERVICE_KEY)
    monkeypatch.setenv("IDENTITY_MOCK_AVAILABLE", "true")
    monkeypatch.delenv("IDENTITY_LAB_ENABLED", raising=False)
    monkeypatch.delenv("IDENTITY_LAB_FIXTURES_PATH", raising=False)

    with TestClient(create_app()) as client:
        baseline = load_fixtures()
        assert set(baseline) == {
            "participant",
            "participantTwo",
            "organizer",
            "organizerUece",
            "organizerAcademicSim",
            "globalAdmin",
        }
        response = client.post(
            IDENTITY_URL,
            headers={"X-Service-Key": SERVICE_KEY},
            json={"token": "synthetic-scenario-participant-01"},
        )

    assert response.status_code == 401
    assert response.json()["code"] == "INVALID_IDENTITY"


def test_enabled_pool_authenticates_the_full_unique_participant_pool(
    monkeypatch,
):
    monkeypatch.setenv("INTERNAL_SERVICE_KEY", SERVICE_KEY)
    monkeypatch.setenv("IDENTITY_MOCK_AVAILABLE", "true")
    monkeypatch.setenv("IDENTITY_LAB_ENABLED", "true")
    monkeypatch.setenv("IDENTITY_LAB_FIXTURES_PATH", str(FIXTURE_PATH))
    fixtures = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert len(fixtures) == SCENARIO_PARTICIPANT_COUNT
    assert len({identity["token"] for identity in fixtures.values()}) == len(fixtures)
    assert len({identity["subjectId"] for identity in fixtures.values()}) == len(fixtures)
    sample = ("01", "20", "21", "999", str(SCENARIO_PARTICIPANT_COUNT))

    with TestClient(create_app()) as client:
        for identity in (fixtures[f"scenarioParticipant{suffix}"] for suffix in sample):
            response = client.post(
                IDENTITY_URL,
                headers={"X-Service-Key": SERVICE_KEY},
                json={"token": identity["token"]},
            )
            assert response.status_code == 200
            assert response.json()["role"] == "PARTICIPANT"
            assert response.json()["organizationId"] is None
            assert identity["token"] not in response.text


def test_enabled_pool_preserves_the_baseline_persona_roles(monkeypatch):
    monkeypatch.setenv("INTERNAL_SERVICE_KEY", SERVICE_KEY)
    monkeypatch.setenv("IDENTITY_MOCK_AVAILABLE", "true")
    monkeypatch.setenv("IDENTITY_LAB_ENABLED", "true")
    monkeypatch.setenv("IDENTITY_LAB_FIXTURES_PATH", str(FIXTURE_PATH))

    with TestClient(create_app()) as client:
        for token, role in (
            ("synthetic-participant-001", "PARTICIPANT"),
            ("synthetic-participant-002", "PARTICIPANT"),
            ("synthetic-organizer-org-a", "ORGANIZER"),
            ("synthetic-organizer-uece", "ORGANIZER"),
            ("synthetic-organizer-instituto-academico-simulado", "ORGANIZER"),
            ("synthetic-global-admin", "GLOBAL_ADMIN"),
        ):
            response = client.post(
                IDENTITY_URL,
                headers={"X-Service-Key": SERVICE_KEY},
                json={"token": token},
            )
            assert response.status_code == 200
            assert response.json()["role"] == role


@pytest.mark.parametrize(
    "mutate",
    [
        lambda data: data.pop("scenarioParticipant20"),
        lambda data: data.update(
            scenarioParticipant20001={
                "token": "synthetic-scenario-participant-20001",
                "subjectId": "50000000-0000-4000-8000-000000020001",
                "role": "PARTICIPANT",
            }
        ),
        lambda data: data["scenarioParticipant01"].update(role="GLOBAL_ADMIN"),
        lambda data: data["scenarioParticipant01"].update(
            organizationId="30000000-0000-4000-8000-000000000001"
        ),
        lambda data: data["scenarioParticipant01"].update(subjectId="not-a-uuid"),
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
def test_rejects_malformed_oversized_or_privileged_scenario_fixtures(tmp_path, mutate):
    data = _scenario_data()
    mutate(data)
    path = _write_scenario(tmp_path, data)

    with pytest.raises(ValueError):
        load_scenario_fixtures(path, load_fixtures())


def test_rejects_oversized_scenario_fixture_file(tmp_path):
    path = tmp_path / "scenario-participants.json"
    path.write_text(" " * (MAX_SCENARIO_FIXTURE_BYTES + 1), encoding="utf-8")

    with pytest.raises(ValueError):
        load_scenario_fixtures(path, load_fixtures())


def test_rejects_scenario_identity_colliding_with_baseline(tmp_path):
    data = _scenario_data()
    data["scenarioParticipant01"]["token"] = "synthetic-participant-001"
    path = _write_scenario(tmp_path, data)

    with pytest.raises(ValueError):
        load_scenario_fixtures(path, load_fixtures())


def test_rejects_scenario_subject_colliding_with_baseline(tmp_path):
    data = _scenario_data()
    data["scenarioParticipant01"]["subjectId"] = "10000000-0000-4000-8000-000000000001"
    path = _write_scenario(tmp_path, data)

    with pytest.raises(ValueError):
        load_scenario_fixtures(path, load_fixtures())


def test_fixture_subjects_are_valid_uuid_values():
    for identity in json.loads(FIXTURE_PATH.read_text(encoding="utf-8")).values():
        UUID(identity["subjectId"])
