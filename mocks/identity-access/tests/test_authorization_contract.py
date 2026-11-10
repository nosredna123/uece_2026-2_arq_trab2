from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[3]


def test_registration_contract_derives_and_scopes_participant_identity():
    spec = yaml.safe_load(
        (ROOT / "docs/contracts/openapi/registration.yaml").read_text()
    )
    paths = spec["paths"]

    create = paths["/registrations"]["post"]
    assert create["x-roles"] == ["PARTICIPANT"]
    body = create["requestBody"]["content"]["application/json"]["schema"]
    assert body["required"] == ["activityId"]
    assert set(body["properties"]) == {"activityId"}
    assert "Derive participant from validated principal" in create["description"]

    assert paths["/registrations"]["get"]["operationId"] == "listOwnRegistrations"
    assert paths["/registrations/{id}"]["get"]["operationId"] == "getOwnRegistration"
    assert "foreign resource is concealed with 404" in paths["/registrations/{id}"][
        "get"
    ]["description"]


def test_identity_contract_documents_fail_closed_token_resolution():
    spec = yaml.safe_load(
        (ROOT / "docs/contracts/openapi/identity-mock.yaml").read_text()
    )
    operation = spec["paths"]["/internal/identity/validate"]["post"]

    assert operation["x-roles"] == ["domain-service"]
    assert "invalid token is 401" in operation["description"]
    assert "unavailable scenario is 503" in operation["description"]
    assert "Never log token" in operation["description"]
