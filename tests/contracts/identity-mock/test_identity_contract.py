import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[3]


def test_synthetic_fixtures_cover_approved_scopes():
    fixtures = json.loads(
        (
            ROOT
            / "mocks/identity-access/src/identity_access_mock/fixtures.json"
        ).read_text()
    )
    assert set(fixtures) == {
        "participant",
        "participantTwo",
        "organizer",
        "organizerUece",
        "organizerAcademicSim",
        "globalAdmin",
    }
    tokens = [fixture["token"] for fixture in fixtures.values()]
    assert len(set(tokens)) == len(tokens)
    assert all(token.startswith("synthetic-") for token in tokens)
    for participant in ("participant", "participantTwo"):
        assert fixtures[participant]["role"] == "PARTICIPANT"
        assert "organizationId" not in fixtures[participant]
    assert fixtures["organizer"]["role"] == "ORGANIZER"
    assert fixtures["organizer"]["organizationId"]
    organizer_ids = {
        fixtures[name]["organizationId"]
        for name in ("organizer", "organizerUece", "organizerAcademicSim")
    }
    assert len(organizer_ids) == 3
    assert fixtures["globalAdmin"]["role"] == "GLOBAL_ADMIN"
    assert "organizationId" not in fixtures["globalAdmin"]


def test_identity_contract_is_internal_and_rejects_identity_claims():
    spec = yaml.safe_load(
        (ROOT / "docs/contracts/openapi/identity-mock.yaml").read_text()
    )
    operation = spec["paths"]["/internal/identity/validate"]["post"]
    assert operation["x-roles"] == ["domain-service"]
    assert spec["security"] == [{"ServiceAuth": []}]
    assert operation.get("security", spec["security"]) == [{"ServiceAuth": []}]
    body = operation["requestBody"]["content"]["application/json"]["schema"]
    assert body["additionalProperties"] is False
    assert body["required"] == ["token"]
    assert set(body["properties"]) == {"token"}
    assert body["properties"]["token"]["writeOnly"] is True
    assert "401" not in operation["responses"]
    assert "default" in operation["responses"]


def test_principal_contract_does_not_authorize_anonymous_identity():
    spec = yaml.safe_load(
        (ROOT / "docs/contracts/openapi/common.yaml").read_text()
    )
    principal = spec["components"]["schemas"]["Principal"]
    assert principal["required"] == ["subjectId", "role"]
    assert principal["properties"]["role"]["enum"] == [
        "PARTICIPANT", "ORGANIZER", "GLOBAL_ADMIN"
    ]
