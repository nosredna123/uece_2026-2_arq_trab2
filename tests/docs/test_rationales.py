import json
from pathlib import Path
import re

import pytest


ROOT = Path(__file__).resolve().parents[2]
NAMES = (
    "service-boundaries",
    "data-ownership",
    "registration-consistency",
    "messaging-and-resilience",
    "prototype-scope",
    "demo-webapp",
)


@pytest.mark.parametrize("number,name", enumerate(NAMES, 1))
def test_versioned_rationale(number, name):
    path = ROOT / f"docs/rationales/ADR-{number:03}.json"
    value = json.loads(path.read_text())
    assert set(value) == {"artifact", "rationale", "invariants", "version"}
    assert value["artifact"] == f"docs/adr/ADR-{number:03}-{name}.md"
    assert re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", value["version"])
    adr = (ROOT / value["artifact"]).read_text()
    assert f"**Version:** {value['version']}" in adr
    assert isinstance(value["rationale"], str) and value["rationale"].strip()
    assert isinstance(value["invariants"], list) and value["invariants"]
    assert all(isinstance(item, str) and item.strip() for item in value["invariants"])
    assert len(set(value["invariants"])) == len(value["invariants"])


def test_complete_rationale_inventory():
    paths = sorted((ROOT / "docs/rationales").glob("ADR-*.json"))
    assert [path.stem for path in paths] == [f"ADR-{n:03}" for n in range(1, 7)]
    artifacts = [json.loads(path.read_text())["artifact"] for path in paths]
    assert len(set(artifacts)) == 6
