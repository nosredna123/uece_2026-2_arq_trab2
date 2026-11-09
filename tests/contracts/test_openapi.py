from pathlib import Path

from openapi_spec_validator import validate
import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
DIRECTORY = ROOT / "docs/contracts/openapi"
NAMES = (
    "event-management", "registration", "attendance-certification",
    "reporting", "identity-mock",
)


@pytest.mark.parametrize("name", NAMES)
def test_openapi_contract(name):
    path = DIRECTORY / f"{name}.yaml"
    spec = yaml.safe_load(path.read_text())
    validate(spec, base_uri=path.as_uri())
    assert spec["openapi"] == "3.0.3"
    ids = []
    for path_name, path_item in spec["paths"].items():
        assert path_name.startswith("/")
        for method, operation in path_item.items():
            if method == "parameters":
                continue
            ids.append(operation["operationId"])
            assert operation.get("description")
            assert "default" in operation["responses"]
            assert "x-roles" in operation
    assert len(set(ids)) == len(ids)


def test_protected_operations_never_allow_anonymous():
    for name in NAMES:
        spec = yaml.safe_load((DIRECTORY / f"{name}.yaml").read_text())
        assert spec["security"]
        for item in spec["paths"].values():
            for method, operation in item.items():
                if method == "parameters":
                    continue
                if operation["x-roles"] != ["public"]:
                    assert operation.get("security", spec["security"])
