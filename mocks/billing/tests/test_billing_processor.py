import json
import random
import sqlite3
from contextlib import closing

import pytest

from billing_mock.models import InvalidBillingRequest
from billing_mock.processor import (
    BillingProcessor,
    BillingSettings,
    PublishNotConfirmed,
)
from billing_mock.settings import WorkerSettings, parse_delay_range
from billing_mock.store import BillingStore


PAYMENT_OPERATION = "50000000-0000-4000-8000-000000000001"
REFUND_OPERATION = "60000000-0000-4000-8000-000000000001"
REGISTRATION_ID = "70000000-0000-4000-8000-000000000001"
ACTIVITY_ID = "80000000-0000-4000-8000-000000000001"
ORGANIZATION_ID = "30000000-0000-4000-8000-000000000001"
PARTICIPANT_ID = "10000000-0000-4000-8000-000000000001"


def payment_request(
    *,
    operation_id=PAYMENT_OPERATION,
    attempt_id="90000000-0000-4000-8000-000000000001",
    event_id="a0000000-0000-4000-8000-000000000001",
    version=1,
    amount_minor=105,
):
    return {
        "eventId": event_id,
        "eventType": "billing.requested",
        "schemaVersion": "1.0.0",
        "aggregateId": operation_id,
        "aggregateVersion": version,
        "correlationId": "b0000000-0000-4000-8000-000000000001",
        "occurredAt": "2026-11-09T10:00:00Z",
        "producer": "registration",
        "data": {
            "operationId": operation_id,
            "attemptId": attempt_id,
            "kind": "PAYMENT",
            "registrationId": REGISTRATION_ID,
            "activityId": ACTIVITY_ID,
            "organizationId": ORGANIZATION_ID,
            "participantId": PARTICIPANT_ID,
            "amountMinor": amount_minor,
            "currency": "BRL",
            "expiresAt": "2026-11-09T10:30:00Z",
        },
    }


def refund_request(
    *,
    attempt_id="90000000-0000-4000-8000-000000000002",
    event_id="a0000000-0000-4000-8000-000000000002",
    version=1,
    original_payment_id=PAYMENT_OPERATION,
    amount_minor=105,
):
    event = payment_request(
        operation_id=REFUND_OPERATION,
        attempt_id=attempt_id,
        event_id=event_id,
        version=version,
        amount_minor=amount_minor,
    )
    event["data"]["kind"] = "REFUND"
    event["data"]["originalPaymentId"] = original_payment_id
    event["data"].pop("expiresAt")
    return event


def processor(tmp_path, *, payment="APPROVED", refund="APPROVED", percentage=90):
    store = BillingStore(tmp_path / "billing.sqlite3")
    settings = BillingSettings(
        payment_scenario=payment,
        refund_scenario=refund,
        refund_percentage=percentage,
    )
    return BillingProcessor(settings, store), store


def process(processor, message):
    return processor.process_message(json.dumps(message).encode())


def test_approved_payment_is_persisted_and_same_attempt_replays(tmp_path):
    worker, _ = processor(tmp_path)
    request = payment_request()

    result = process(worker, request)
    restarted, restarted_store = processor(tmp_path)
    replay = process(restarted, request)

    assert result == replay
    assert result["eventType"] == "billing.result"
    assert result["producer"] == "billing-mock"
    assert result["aggregateId"] == PAYMENT_OPERATION
    assert result["aggregateVersion"] == 1
    assert result["data"]["status"] == "APPROVED"
    assert result["data"]["attemptId"] == request["data"]["attemptId"]
    assert result["data"]["amountMinor"] == 105
    assert set(result) == {
        "eventId",
        "eventType",
        "schemaVersion",
        "aggregateId",
        "aggregateVersion",
        "correlationId",
        "occurredAt",
        "producer",
        "data",
    }
    assert set(result["data"]) == {
        "operationId",
        "attemptId",
        "kind",
        "registrationId",
        "activityId",
        "organizationId",
        "status",
        "amountMinor",
        "currency",
    }
    assert len(restarted_store.pending_outbox()) == 1
    assert restarted_store.count_attempts() == 1


@pytest.mark.parametrize(
    ("scenario", "expected"),
    [("REFUSED", "REFUSED"), ("APPROVED", "APPROVED")],
)
def test_payment_scenario_selects_the_persisted_outcome(tmp_path, scenario, expected):
    worker, _ = processor(tmp_path, payment=scenario)

    result = process(worker, payment_request())

    assert result["data"]["status"] == expected


def test_timeout_persists_no_result_and_only_a_new_attempt_uses_new_scenario(tmp_path):
    timed_out, store = processor(tmp_path, payment="TIMEOUT")
    request = payment_request()
    assert process(timed_out, request) is None

    recovered, _ = processor(tmp_path, payment="APPROVED")
    assert process(recovered, request) is None
    assert store.pending_outbox() == []

    retry = payment_request(
        attempt_id="90000000-0000-4000-8000-000000000003",
        event_id="a0000000-0000-4000-8000-000000000003",
        version=2,
    )
    result = process(recovered, retry)
    assert result["data"]["status"] == "APPROVED"
    assert result["data"]["attemptId"] == retry["data"]["attemptId"]
    assert store.count_attempts() == 2


def test_expired_payment_can_emit_a_late_approval_for_registration_to_reconcile(tmp_path):
    worker, _ = processor(tmp_path)
    request = payment_request()
    request["occurredAt"] = "2000-01-01T00:00:00Z"
    request["data"]["expiresAt"] = "2000-01-01T00:30:00Z"

    result = process(worker, request)

    assert result["data"]["status"] == "APPROVED"
    assert result["data"]["attemptId"] == request["data"]["attemptId"]


def test_refund_timeout_can_be_retried_with_a_new_attempt(tmp_path):
    payment_worker, store = processor(tmp_path)
    process(payment_worker, payment_request())
    timed_out = BillingProcessor(
        BillingSettings("APPROVED", "TIMEOUT", 90),
        store,
    )
    request = refund_request()
    assert process(timed_out, request) is None

    recovered = BillingProcessor(
        BillingSettings("APPROVED", "APPROVED", 90),
        store,
    )
    assert process(recovered, request) is None
    retry = refund_request(
        attempt_id="90000000-0000-4000-8000-000000000003",
        event_id="a0000000-0000-4000-8000-000000000003",
        version=2,
    )
    result = process(recovered, retry)
    assert result["data"]["status"] == "APPROVED"
    assert result["data"]["amountMinor"] == 95


def test_new_attempt_for_successful_operation_replays_without_second_operation(
    tmp_path,
):
    worker, store = processor(tmp_path)
    first = process(worker, payment_request())
    retry = payment_request(
        attempt_id="90000000-0000-4000-8000-000000000003",
        event_id="a0000000-0000-4000-8000-000000000003",
        version=2,
    )

    replay = process(worker, retry)

    assert replay["data"]["status"] == "APPROVED"
    assert replay["data"]["attemptId"] == retry["data"]["attemptId"]
    assert replay["eventId"] != first["eventId"]
    assert replay["aggregateVersion"] == 2
    assert store.count_operations() == 1
    assert store.count_attempts() == 2


def test_transport_identity_is_checked_as_uuid_and_request_versions_increase(tmp_path):
    worker, _ = processor(tmp_path)
    request = payment_request()

    result = worker.process_message(
        json.dumps(request).encode(),
        transport_message_id=request["eventId"].upper(),
        transport_correlation_id=request["correlationId"].upper(),
    )
    assert result is not None

    retry = payment_request(
        attempt_id="90000000-0000-4000-8000-000000000003",
        event_id="a0000000-0000-4000-8000-000000000003",
        version=1,
    )
    with pytest.raises(InvalidBillingRequest):
        process(worker, retry)

    with pytest.raises(InvalidBillingRequest):
        worker.process_message(
            json.dumps(retry).encode(),
            transport_message_id="c0000000-0000-4000-8000-000000000001",
            transport_correlation_id=retry["correlationId"],
        )


def test_refund_uses_separate_scenario_and_half_up_integer_percentage(tmp_path):
    payment_worker, store = processor(tmp_path)
    process(payment_worker, payment_request())
    refund_worker = BillingProcessor(
        BillingSettings(
            payment_scenario="REFUSED",
            refund_scenario="APPROVED",
            refund_percentage=50,
        ),
        store,
    )

    result = process(refund_worker, refund_request())

    assert result["data"]["kind"] == "REFUND"
    assert result["data"]["status"] == "APPROVED"
    assert result["data"]["amountMinor"] == 53


def test_refund_requires_a_matching_successful_original_payment(tmp_path):
    worker, _ = processor(tmp_path)
    request = refund_request(original_payment_id="c0000000-0000-4000-8000-000000000001")

    with pytest.raises(InvalidBillingRequest):
        process(worker, request)


def test_conflicting_attempt_or_operation_payload_is_rejected(tmp_path):
    worker, _ = processor(tmp_path)
    original = payment_request()
    process(worker, original)

    changed_attempt = payment_request(amount_minor=106)
    with pytest.raises(InvalidBillingRequest):
        process(worker, changed_attempt)

    changed_operation = payment_request(
        attempt_id="90000000-0000-4000-8000-000000000003",
        event_id="a0000000-0000-4000-8000-000000000004",
        version=2,
        amount_minor=106,
    )
    with pytest.raises(InvalidBillingRequest):
        process(worker, changed_operation)


def test_second_successful_refund_operation_cannot_refund_payment_again(tmp_path):
    worker, store = processor(tmp_path, percentage=100)
    process(worker, payment_request())
    process(worker, refund_request())
    duplicate_refund = refund_request(
        attempt_id="90000000-0000-4000-8000-000000000003",
        event_id="a0000000-0000-4000-8000-000000000003",
        version=2,
    )
    duplicate_refund["data"]["operationId"] = (
        "60000000-0000-4000-8000-000000000002"
    )
    duplicate_refund["aggregateId"] = duplicate_refund["data"]["operationId"]

    with pytest.raises(InvalidBillingRequest):
        process(worker, duplicate_refund)

    assert store.count_operations() == 2


def test_publisher_confirm_marks_outbox_and_uses_persistent_message_properties(
    tmp_path,
):
    worker, store = processor(tmp_path)
    result = process(worker, payment_request())

    class ConfirmingChannel:
        def __init__(self):
            self.published = []

        def basic_publish(self, **kwargs):
            self.published.append(kwargs)
            return True

    channel = ConfirmingChannel()
    assert worker.publish_pending(channel) == 1
    assert worker.publish_pending(channel) == 0
    assert channel.published[0]["routing_key"] == "billing.result"
    assert channel.published[0]["mandatory"] is True
    assert channel.published[0]["properties"].delivery_mode == 2
    assert channel.published[0]["properties"].message_id == result["eventId"]
    assert store.count_unpublished() == 0


def test_unconfirmed_publication_leaves_outbox_pending(tmp_path):
    worker, store = processor(tmp_path)
    process(worker, payment_request())

    class UnconfirmedChannel:
        def basic_publish(self, **kwargs):
            assert kwargs["mandatory"] is True
            return False

    with pytest.raises(PublishNotConfirmed):
        worker.publish_pending(UnconfirmedChannel())

    assert store.count_unpublished() == 1


@pytest.mark.parametrize(
    "change",
    [
        {"data": {"kind": "PAYMENT", "amountMinor": 0}},
        {"data": {"kind": "REFUND", "amountMinor": 1}},
    ],
)
def test_invalid_operation_schema_is_rejected_without_processing(tmp_path, change):
    worker, store = processor(tmp_path)
    request = payment_request()
    request["data"].update(change["data"])
    if request["data"]["kind"] == "REFUND":
        request["data"]["originalPaymentId"] = PAYMENT_OPERATION
        request["data"].pop("expiresAt")

    with pytest.raises(InvalidBillingRequest):
        process(worker, request)

    assert store.count_attempts() == 0


def test_non_utc_timestamps_are_rejected(tmp_path):
    worker, store = processor(tmp_path)
    request = payment_request()
    request["occurredAt"] = "2026-11-09T10:00:00+01:00"

    with pytest.raises(InvalidBillingRequest):
        process(worker, request)

    assert store.count_attempts() == 0


def test_snake_case_payload_aliases_are_rejected(tmp_path):
    worker, store = processor(tmp_path)
    request = payment_request()
    request["data"]["operation_id"] = request["data"].pop("operationId")

    with pytest.raises(InvalidBillingRequest):
        process(worker, request)

    assert store.count_attempts() == 0


def test_worker_settings_require_valid_inputs_and_hide_broker_password(monkeypatch, tmp_path):
    settings = {
        "RABBITMQ_HOST": "rabbitmq",
        "RABBITMQ_PORT": "5672",
        "RABBITMQ_USER": "eventhub_billing",
        "RABBITMQ_PASSWORD": "synthetic-broker-secret",
        "BILLING_MOCK_SCENARIO": "APPROVED",
        "BILLING_MOCK_REFUND_SCENARIO": "TIMEOUT",
        "REFUND_PERCENTAGE": "90",
        "BILLING_STORE_PATH": str(tmp_path / "billing.sqlite3"),
        "MESSAGE_RETRY_INITIAL_SECONDS": "1",
        "MESSAGE_RETRY_MAX_SECONDS": "60",
    }
    for key, value in settings.items():
        monkeypatch.setenv(key, value)

    configured = WorkerSettings.from_environment()
    assert configured.billing.payment_scenario == "APPROVED"
    assert "synthetic-broker-secret" not in repr(configured)

    monkeypatch.setenv("BILLING_MOCK_SCENARIO", "UNKNOWN")
    with pytest.raises(RuntimeError, match="BILLING_MOCK_SCENARIO"):
        WorkerSettings.from_environment()


class RecordingChannel:
    def __init__(self):
        self.published = []

    def basic_publish(self, **kwargs):
        self.published.append(json.loads(kwargs["body"]))
        return True


def test_payment_results_wait_for_the_configured_delay(tmp_path):
    now = [1000.0]
    store = BillingStore(tmp_path / "billing.sqlite3")
    worker = BillingProcessor(
        BillingSettings("APPROVED", "APPROVED", 90, payment_delay_seconds=(2, 8)),
        store,
        rng=random.Random(1),
        clock=lambda: now[0],
    )
    process(worker, payment_request())
    channel = RecordingChannel()

    assert worker.publish_pending(channel) == 0
    now[0] += 1.99
    assert worker.publish_pending(channel) == 0
    assert store.count_unpublished() == 1
    now[0] += 6.02
    assert worker.publish_pending(channel) == 1
    assert channel.published[0]["data"]["status"] == "APPROVED"

    process(worker, refund_request())
    assert worker.publish_pending(channel) == 1
    assert channel.published[1]["data"]["kind"] == "REFUND"


def test_zero_delay_publishes_payment_results_at_once(tmp_path):
    worker, _ = processor(tmp_path)
    process(worker, payment_request())
    assert worker.publish_pending(RecordingChannel()) == 1


def test_existing_store_gains_the_delay_column(tmp_path):
    path = tmp_path / "billing.sqlite3"
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(
            "CREATE TABLE outbox (event_id TEXT PRIMARY KEY, body TEXT NOT NULL, "
            "correlation_id TEXT NOT NULL, published_at TEXT)"
        )
        connection.execute("INSERT INTO outbox VALUES ('e1', '{}', 'c1', NULL)")
        connection.commit()

    store = BillingStore(path)

    assert [message.event_id for message in store.pending_outbox()] == ["e1"]


@pytest.mark.parametrize(
    ("value", "expected"),
    [("0", (0, 0)), ("5", (5, 5)), ("2-8", (2, 8)), (" 0-60 ", (0, 60))],
)
def test_payment_delay_accepts_a_value_or_a_range(value, expected):
    assert parse_delay_range(value) == expected


@pytest.mark.parametrize("value", ["", "-1", "8-2", "1-61", "a", "1-2-3", "1.5"])
def test_payment_delay_rejects_invalid_values(value):
    with pytest.raises(ValueError):
        parse_delay_range(value)


def test_worker_settings_read_the_payment_delay(monkeypatch, tmp_path):
    for key, value in {
        "RABBITMQ_HOST": "rabbitmq",
        "RABBITMQ_PORT": "5672",
        "RABBITMQ_USER": "eventhub_billing",
        "RABBITMQ_PASSWORD": "synthetic-broker-secret",
        "BILLING_MOCK_SCENARIO": "APPROVED",
        "BILLING_MOCK_REFUND_SCENARIO": "APPROVED",
        "REFUND_PERCENTAGE": "90",
        "BILLING_STORE_PATH": str(tmp_path / "billing.sqlite3"),
        "MESSAGE_RETRY_INITIAL_SECONDS": "1",
        "MESSAGE_RETRY_MAX_SECONDS": "60",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("BILLING_MOCK_APPROVAL_DELAY_SECONDS", raising=False)
    assert WorkerSettings.from_environment().billing.payment_delay_seconds == (0, 0)

    monkeypatch.setenv("BILLING_MOCK_APPROVAL_DELAY_SECONDS", "2-8")
    assert WorkerSettings.from_environment().billing.payment_delay_seconds == (2, 8)

    monkeypatch.setenv("BILLING_MOCK_APPROVAL_DELAY_SECONDS", "9-3")
    with pytest.raises(RuntimeError, match="BILLING_MOCK_APPROVAL_DELAY_SECONDS"):
        WorkerSettings.from_environment()


@pytest.mark.parametrize(("percent", "expected"), [(0, "APPROVED"), (100, "REFUSED")])
def test_refusal_percent_refuses_payments_at_random(tmp_path, percent, expected):
    worker = BillingProcessor(
        BillingSettings("APPROVED", "APPROVED", 90, payment_refusal_percent=percent),
        BillingStore(tmp_path / "billing.sqlite3"),
        rng=random.Random(1),
    )

    assert process(worker, payment_request())["data"]["status"] == expected
    if expected == "APPROVED":
        assert process(worker, refund_request())["data"]["status"] == "APPROVED"


def test_refusal_percent_mixes_outcomes(tmp_path):
    worker = BillingProcessor(
        BillingSettings("APPROVED", "APPROVED", 90, payment_refusal_percent=50),
        BillingStore(tmp_path / "billing.sqlite3"),
        rng=random.Random(3),
    )
    statuses = {
        process(
            worker,
            payment_request(
                operation_id=f"80000000-0000-4000-8000-{index:012d}",
                attempt_id=f"90000000-0000-4000-8000-{index:012d}",
                event_id=f"a0000000-0000-4000-8000-{index:012d}",
            ),
        )["data"]["status"]
        for index in range(10, 30)
    }

    assert statuses == {"APPROVED", "REFUSED"}


@pytest.mark.parametrize("percent", [-1, 101])
def test_refusal_percent_must_be_a_percentage(percent):
    with pytest.raises(ValueError, match="refusal"):
        BillingSettings("APPROVED", "APPROVED", 90, payment_refusal_percent=percent)


def test_worker_settings_read_the_refusal_percent(monkeypatch, tmp_path):
    for key, value in {
        "RABBITMQ_HOST": "rabbitmq",
        "RABBITMQ_PORT": "5672",
        "RABBITMQ_USER": "eventhub_billing",
        "RABBITMQ_PASSWORD": "synthetic-broker-secret",
        "BILLING_MOCK_SCENARIO": "APPROVED",
        "BILLING_MOCK_REFUND_SCENARIO": "APPROVED",
        "REFUND_PERCENTAGE": "90",
        "BILLING_STORE_PATH": str(tmp_path / "billing.sqlite3"),
        "MESSAGE_RETRY_INITIAL_SECONDS": "1",
        "MESSAGE_RETRY_MAX_SECONDS": "60",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("BILLING_MOCK_PAYMENT_REFUSAL_PERCENT", raising=False)
    assert WorkerSettings.from_environment().billing.payment_refusal_percent == 0

    monkeypatch.setenv("BILLING_MOCK_PAYMENT_REFUSAL_PERCENT", "10")
    assert WorkerSettings.from_environment().billing.payment_refusal_percent == 10

    monkeypatch.setenv("BILLING_MOCK_PAYMENT_REFUSAL_PERCENT", "101")
    with pytest.raises(RuntimeError, match="BILLING_MOCK_PAYMENT_REFUSAL_PERCENT"):
        WorkerSettings.from_environment()
