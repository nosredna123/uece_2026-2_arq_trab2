from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def test_registration_states_match_message_contract():
    http = yaml.safe_load(
        (ROOT / "docs/contracts/openapi/registration.yaml").read_text()
    )
    text = (ROOT / "docs/architecture/data-ownership.md").read_text()
    states = http["components"]["schemas"]["Registration"]["properties"]["status"]["enum"]
    for state in states:
        assert state in text
    for section in (
        "Physical and access boundaries", "Event Management",
        "Registration records and constraints", "Idempotency, inbox and outbox",
        "Attendance and Reporting projections", "Migration and retention policy",
    ):
        assert f"## {section}" in text


def test_recovery_and_ownership_obligations_are_explicit():
    text = (ROOT / "docs/architecture/data-ownership.md").read_text()
    for phrase in (
        "No automatic event-wide refund rule",
        "clock_timestamp() >= expiresAt",
        "no automatic business-data/message deduplication purge",
        "same activity lock", "same-version",
        "No network I/O", "one active enrollment", "refund PENDING",
        "CONFIRMED, refund PENDING / refund approval",
        "No receipt is committed for a rolled-back",
    ):
        assert phrase in text
