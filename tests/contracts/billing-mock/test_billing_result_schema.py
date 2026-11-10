import json
from pathlib import Path
import uuid

from jsonschema import Draft7Validator, FormatChecker
from referencing import Registry, Resource

from billing_mock.processor import BillingProcessor, BillingSettings
from billing_mock.store import BillingStore


ROOT = Path(__file__).resolve().parents[3]
SCHEMA_DIRECTORY = ROOT / "docs/contracts/schemas"


def validate_result(result: dict[str, object]) -> None:
    resources = {
        path.as_uri(): Resource.from_contents(json.loads(path.read_text()))
        for path in SCHEMA_DIRECTORY.glob("*.json")
    }
    registry = Registry().with_resources(resources.items())
    validator = Draft7Validator(
        {"$ref": (SCHEMA_DIRECTORY / "billing-result.json").as_uri()},
        registry=registry,
        format_checker=FormatChecker(),
    )
    validator.validate(result)


def payment_request(operation_id: str, attempt_id: str) -> bytes:
    event = {
        "eventId": str(uuid.uuid4()),
        "eventType": "billing.requested",
        "schemaVersion": "1.0.0",
        "aggregateId": operation_id,
        "aggregateVersion": 1,
        "correlationId": str(uuid.uuid4()),
        "occurredAt": "2026-11-09T10:00:00Z",
        "producer": "registration",
        "data": {
            "operationId": operation_id,
            "attemptId": attempt_id,
            "kind": "PAYMENT",
            "registrationId": "70000000-0000-4000-8000-000000000001",
            "activityId": "80000000-0000-4000-8000-000000000001",
            "organizationId": "30000000-0000-4000-8000-000000000001",
            "participantId": "10000000-0000-4000-8000-000000000001",
            "amountMinor": 105,
            "currency": "BRL",
            "expiresAt": "2026-11-09T10:30:00Z",
        },
    }
    return json.dumps(event).encode()


def test_published_payment_result_matches_the_asyncapi_schema(tmp_path):
    operation_id = "50000000-0000-4000-8000-000000000001"
    attempt_id = "90000000-0000-4000-8000-000000000001"
    processor = BillingProcessor(
        BillingSettings("APPROVED", "APPROVED", 90),
        BillingStore(tmp_path / "billing.sqlite3"),
    )

    result = processor.process_message(payment_request(operation_id, attempt_id))

    assert result is not None
    validate_result(result)


def test_published_refund_result_matches_the_asyncapi_schema(tmp_path):
    operation_id = "50000000-0000-4000-8000-000000000001"
    attempt_id = "90000000-0000-4000-8000-000000000001"
    refund_operation_id = "60000000-0000-4000-8000-000000000001"
    refund_attempt_id = "90000000-0000-4000-8000-000000000002"
    store = BillingStore(tmp_path / "billing.sqlite3")
    processor = BillingProcessor(
        BillingSettings("APPROVED", "APPROVED", 90),
        store,
    )
    processor.process_message(payment_request(operation_id, attempt_id))

    refund = json.loads(
        payment_request(refund_operation_id, refund_attempt_id)
    )
    refund["eventId"] = str(uuid.uuid4())
    refund["aggregateId"] = refund_operation_id
    refund["data"]["kind"] = "REFUND"
    refund["data"]["originalPaymentId"] = operation_id
    del refund["data"]["expiresAt"]
    result = processor.process_message(json.dumps(refund).encode())

    assert result is not None
    assert result["data"]["amountMinor"] == 95
    validate_result(result)
