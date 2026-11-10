import json

import pytest

from notification_mock.models import InvalidNotificationEvent
from notification_mock.processor import NotificationProcessor
from notification_mock.settings import NotificationSettings, WorkerSettings
from notification_mock.store import NotificationStore
from notification_mock.worker import retry_delay


REGISTRATION_ID = "70000000-0000-4000-8000-000000000001"
ACTIVITY_ID = "80000000-0000-4000-8000-000000000001"
ORGANIZATION_ID = "30000000-0000-4000-8000-000000000001"
PARTICIPANT_ID = "10000000-0000-4000-8000-000000000001"
EVENT_ID = "a0000000-0000-4000-8000-000000000001"
CORRELATION_ID = "b0000000-0000-4000-8000-000000000001"


def registration_event(
    *,
    event_id=EVENT_ID,
    status="CONFIRMED",
    refund_status="NONE",
):
    return {
        "eventId": event_id,
        "eventType": "registration.changed",
        "schemaVersion": "1.0.0",
        "aggregateId": REGISTRATION_ID,
        "aggregateVersion": 1,
        "correlationId": CORRELATION_ID,
        "occurredAt": "2026-11-09T10:00:00Z",
        "producer": "registration",
        "data": {
            "registrationId": REGISTRATION_ID,
            "activityId": ACTIVITY_ID,
            "organizationId": ORGANIZATION_ID,
            "participantId": PARTICIPANT_ID,
            "status": status,
            "refundStatus": refund_status,
            "amountMinor": 0,
            "currency": "BRL",
            "activityStartsAt": "2026-12-01T10:00:00Z",
            "activityEndsAt": "2026-12-01T12:00:00Z",
        },
    }


def processor(tmp_path, *, available=True):
    store = NotificationStore(tmp_path / "notification.sqlite3")
    worker = NotificationProcessor(NotificationSettings(available), store)
    return worker, store


def process(worker, message):
    return worker.process_message(json.dumps(message).encode())


def test_successful_delivery_is_correlated_and_idempotent(tmp_path):
    worker, store = processor(tmp_path)
    event = registration_event()

    result = process(worker, event)
    replay = process(worker, event)

    assert result.status == "DELIVERED"
    assert replay.status == "DELIVERED"
    assert result.event_type == "registration.changed"
    assert result.registration_id == REGISTRATION_ID
    assert store.count_deliveries() == 1
    delivery = store.get_delivery(EVENT_ID)
    assert delivery is not None
    assert delivery.correlation_id == CORRELATION_ID
    assert delivery.notice_type == "registration.confirmed"


def test_refund_snapshot_derives_a_refund_notice(tmp_path):
    worker, store = processor(tmp_path)

    process(worker, registration_event(refund_status="APPROVED"))

    delivery = store.get_delivery(EVENT_ID)
    assert delivery is not None
    assert delivery.notice_type == "registration.refund_approved"


def test_unavailable_attempt_is_diagnostic_and_retryable(tmp_path):
    unavailable, store = processor(tmp_path, available=False)
    event = registration_event()

    result = process(unavailable, event)

    assert result.status == "RETRYABLE"
    assert result.attempt_count == 1
    assert store.count_deliveries() == 0
    inbox_record = store.get_inbox(EVENT_ID)
    assert inbox_record is not None
    assert inbox_record.status == "RETRYABLE"
    assert inbox_record.last_error == "NOTIFICATION_MOCK_UNAVAILABLE"


@pytest.mark.parametrize(
    ("event_type", "producer", "aggregate_id", "data", "notice_type"),
    [
        (
            "activity.changed",
            "event-management",
            ACTIVITY_ID,
            {
                "activityId": ACTIVITY_ID,
                "eventId": "90000000-0000-4000-8000-000000000001",
                "organizationId": ORGANIZATION_ID,
                "status": "CANCELLED",
                "startsAt": "2026-12-01T10:00:00Z",
                "endsAt": "2026-12-01T12:00:00Z",
            },
            "activity.cancelled",
        ),
        (
            "attendance.recorded",
            "attendance-certification",
            REGISTRATION_ID,
            {
                "registrationId": REGISTRATION_ID,
                "activityId": ACTIVITY_ID,
                "organizationId": ORGANIZATION_ID,
                "participantId": PARTICIPANT_ID,
                "checkedInAt": "2026-12-01T10:00:00Z",
            },
            "attendance.recorded",
        ),
        (
            "certificate.issued",
            "attendance-certification",
            REGISTRATION_ID,
            {
                "certificateId": "90000000-0000-4000-8000-000000000002",
                "registrationId": REGISTRATION_ID,
                "activityId": ACTIVITY_ID,
                "organizationId": ORGANIZATION_ID,
                "participantId": PARTICIPANT_ID,
                "issuedAt": "2026-12-01T12:00:00Z",
            },
            "certificate.issued",
        ),
    ],
)
def test_each_supported_source_event_derives_a_notice(
    tmp_path, event_type, producer, aggregate_id, data, notice_type
):
    worker, store = processor(tmp_path)
    event = {
        "eventId": EVENT_ID,
        "eventType": event_type,
        "schemaVersion": "1.0.0",
        "aggregateId": aggregate_id,
        "aggregateVersion": 1,
        "correlationId": CORRELATION_ID,
        "occurredAt": "2026-11-09T10:00:00Z",
        "producer": producer,
        "data": data,
    }

    result = process(worker, event)

    assert result.status == "DELIVERED"
    delivery = store.get_delivery(EVENT_ID)
    assert delivery is not None
    assert delivery.notice_type == notice_type


def test_duplicate_event_with_changed_content_is_rejected(tmp_path):
    worker, _ = processor(tmp_path)
    process(worker, registration_event())
    conflicting = registration_event(status="CANCELLED")

    with pytest.raises(InvalidNotificationEvent):
        process(worker, conflicting)


def test_registration_deadline_requirements_match_the_contract(tmp_path):
    worker, _ = processor(tmp_path)

    with pytest.raises(InvalidNotificationEvent):
        process(worker, registration_event(status="PENDING_PAYMENT"))


def test_transport_identity_must_match_the_event_envelope(tmp_path):
    worker, _ = processor(tmp_path)

    with pytest.raises(InvalidNotificationEvent):
        worker.process_message(
            json.dumps(registration_event()).encode(),
            transport_message_id="c0000000-0000-4000-8000-000000000001",
            transport_correlation_id=CORRELATION_ID,
        )


def test_aggregate_identity_must_match_the_source_fact(tmp_path):
    worker, _ = processor(tmp_path)
    event = registration_event()
    event["aggregateId"] = ACTIVITY_ID

    with pytest.raises(InvalidNotificationEvent):
        process(worker, event)


def test_business_timestamps_accept_offsets_but_occurred_at_must_be_utc(tmp_path):
    worker, store = processor(tmp_path)
    event = registration_event()
    event["data"]["activityStartsAt"] = "2026-12-01T10:00:00-03:00"
    event["data"]["activityEndsAt"] = "2026-12-01T12:00:00-03:00"

    assert process(worker, event).status == "DELIVERED"
    assert store.count_deliveries() == 1

    event["eventId"] = "a0000000-0000-4000-8000-000000000002"
    event["occurredAt"] = "2026-11-09T10:00:00-03:00"
    with pytest.raises(InvalidNotificationEvent):
        process(worker, event)


def test_retry_delay_is_exponential_and_capped():
    assert retry_delay(initial_seconds=3, maximum_seconds=7, attempts=1) == 3
    assert retry_delay(initial_seconds=3, maximum_seconds=7, attempts=2) == 6
    assert retry_delay(initial_seconds=3, maximum_seconds=7, attempts=3) == 7


def test_worker_settings_reject_invalid_boolean_and_missing_store_path(monkeypatch):
    monkeypatch.setenv("NOTIFICATION_MOCK_AVAILABLE", "yes")
    monkeypatch.setenv("NOTIFICATION_STORE_PATH", "/tmp/notification.sqlite3")
    monkeypatch.setenv("RABBITMQ_HOST", "rabbitmq")
    monkeypatch.setenv("RABBITMQ_PORT", "5672")
    monkeypatch.setenv("RABBITMQ_USER", "synthetic")
    monkeypatch.setenv("RABBITMQ_PASSWORD", "synthetic-only")
    monkeypatch.setenv("MESSAGE_RETRY_INITIAL_SECONDS", "1")
    monkeypatch.setenv("MESSAGE_RETRY_MAX_SECONDS", "60")

    with pytest.raises(RuntimeError, match="NOTIFICATION_MOCK_AVAILABLE"):
        WorkerSettings.from_environment()

    monkeypatch.setenv("NOTIFICATION_MOCK_AVAILABLE", "true")
    monkeypatch.delenv("NOTIFICATION_STORE_PATH")
    with pytest.raises(RuntimeError, match="NOTIFICATION_STORE_PATH"):
        WorkerSettings.from_environment()

    monkeypatch.setenv("NOTIFICATION_STORE_PATH", "/data/notification.sqlite3")
    settings = WorkerSettings.from_environment()
    assert settings.available is True
    assert settings.store_path.as_posix() == "/data/notification.sqlite3"
    assert "synthetic-only" not in repr(settings)
