from copy import deepcopy
import json
from pathlib import Path

from jsonschema import Draft7Validator, FormatChecker
import pytest
from referencing import Registry, Resource
import yaml


ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = ROOT / "docs/contracts/schemas"
UUID = "00000000-0000-4000-8000-000000000001"
TIME = "2026-11-09T12:00:00Z"
BASE = {
    "eventId": UUID, "schemaVersion": "1.0.0", "aggregateId": UUID,
    "aggregateVersion": 1, "correlationId": UUID, "occurredAt": TIME,
}
CASES = {
    "billing-request": ("billing.requested", "registration", {
        "operationId": UUID, "attemptId": UUID, "kind": "PAYMENT",
        "registrationId": UUID, "activityId": UUID, "organizationId": UUID,
        "participantId": UUID, "amountMinor": 100, "currency": "BRL",
        "expiresAt": TIME,
    }),
    "billing-result": ("billing.result", "billing-mock", {
        "operationId": UUID, "attemptId": UUID, "kind": "PAYMENT",
        "registrationId": UUID, "activityId": UUID, "organizationId": UUID,
        "status": "APPROVED", "amountMinor": 100, "currency": "BRL",
    }),
    "registration-changed": ("registration.changed", "registration", {
        "registrationId": UUID, "activityId": UUID, "organizationId": UUID,
        "participantId": UUID, "status": "CONFIRMED", "refundStatus": "NONE",
        "amountMinor": 0, "currency": "BRL", "activityStartsAt": TIME,
        "activityEndsAt": TIME,
    }),
    "activity-changed": ("activity.changed", "event-management", {
        "activityId": UUID, "eventId": UUID, "organizationId": UUID,
        "status": "PUBLISHED", "startsAt": TIME, "endsAt": TIME,
    }),
    "attendance-recorded": ("attendance.recorded", "attendance-certification", {
        "registrationId": UUID, "activityId": UUID, "organizationId": UUID,
        "participantId": UUID, "checkedInAt": TIME,
    }),
    "certificate-issued": ("certificate.issued", "attendance-certification", {
        "certificateId": UUID, "registrationId": UUID, "activityId": UUID,
        "organizationId": UUID, "participantId": UUID, "issuedAt": TIME,
    }),
}


def validator(name):
    schemas = {
        path.as_uri(): Resource.from_contents(json.loads(path.read_text()))
        for path in SCHEMAS.glob("*.json")
    }
    registry = Registry().with_resources(schemas.items())
    schema = {"$ref": (SCHEMAS / f"{name}.json").as_uri()}
    return Draft7Validator(schema, registry=registry, format_checker=FormatChecker())


def message(name):
    event_type, producer, data = CASES[name]
    return {**BASE, "eventType": event_type, "producer": producer, "data": deepcopy(data)}


@pytest.mark.parametrize("name", CASES)
def test_valid_payload_and_invalid_envelopes(name):
    check = validator(name)
    sample = message(name)
    check.validate(sample)
    for key in BASE:
        invalid = deepcopy(sample)
        del invalid[key]
        assert not check.is_valid(invalid), key
    for key, value in (("eventId", "not-a-uuid"), ("schemaVersion", "2.0.0"),
                       ("aggregateVersion", 0), ("extra", True)):
        invalid = {**sample, key: value}
        assert not check.is_valid(invalid), key
    invalid = deepcopy(sample)
    invalid["data"]["unexpected"] = True
    assert not check.is_valid(invalid)


def test_payment_and_refund_requirements():
    check = validator("billing-request")
    sample = message("billing-request")
    del sample["data"]["expiresAt"]
    assert not check.is_valid(sample)
    sample["data"]["kind"] = "REFUND"
    assert not check.is_valid(sample)
    sample["data"]["originalPaymentId"] = UUID
    sample["data"]["amountMinor"] = 0
    check.validate(sample)
    sample["data"]["kind"] = "PAYMENT"
    sample["data"]["expiresAt"] = TIME
    assert not check.is_valid(sample)


def test_pending_and_offer_require_deadlines():
    check = validator("registration-changed")
    for status, field in (("OFFERED", "offerExpiresAt"),
                          ("PENDING_PAYMENT", "expiresAt")):
        sample = message("registration-changed")
        sample["data"]["status"] = status
        assert not check.is_valid(sample)
        sample["data"][field] = TIME
        check.validate(sample)


def test_asyncapi_message_inventory():
    spec = yaml.safe_load(
        (ROOT / "docs/contracts/asyncapi/eventhub.yaml").read_text()
    )
    assert spec["asyncapi"] == "2.6.0"
    assert len(spec["channels"]) == len(spec["components"]["messages"]) == 6
    for item in spec["components"]["messages"].values():
        path = (ROOT / "docs/contracts/asyncapi" / item["payload"]["$ref"]).resolve()
        Draft7Validator.check_schema(json.loads(path.read_text()))
        assert item["correlationId"]["location"] == "$message.payload#/correlationId"
