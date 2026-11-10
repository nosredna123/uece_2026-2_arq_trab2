import json
from types import SimpleNamespace

import pika

from notification_mock.processor import NotificationProcessor
from notification_mock.settings import NotificationSettings, WorkerSettings
from notification_mock.store import NotificationStore
from notification_mock.worker import handle_message


def registration_event():
    registration_id = "70000000-0000-4000-8000-000000000001"
    return {
        "eventId": "a0000000-0000-4000-8000-000000000001",
        "eventType": "registration.changed",
        "schemaVersion": "1.0.0",
        "aggregateId": registration_id,
        "aggregateVersion": 1,
        "correlationId": "b0000000-0000-4000-8000-000000000001",
        "occurredAt": "2026-11-09T10:00:00Z",
        "producer": "registration",
        "data": {
            "registrationId": registration_id,
            "activityId": "80000000-0000-4000-8000-000000000001",
            "organizationId": "30000000-0000-4000-8000-000000000001",
            "participantId": "10000000-0000-4000-8000-000000000001",
            "status": "CONFIRMED",
            "refundStatus": "NONE",
            "amountMinor": 0,
            "currency": "BRL",
            "activityStartsAt": "2026-12-01T10:00:00Z",
            "activityEndsAt": "2026-12-01T12:00:00Z",
        },
    }


class ScheduledConnection:
    def __init__(self):
        self.callbacks = []

    def call_later(self, delay, callback):
        self.callbacks.append((delay, callback))


class DeliveryChannel:
    def __init__(self):
        self.connection = ScheduledConnection()
        self.acknowledged = []
        self.requeued = []

    def basic_ack(self, *, delivery_tag):
        self.acknowledged.append(delivery_tag)

    def basic_nack(self, *, delivery_tag, requeue):
        self.requeued.append((delivery_tag, requeue))

    def basic_publish(self, **kwargs):
        return True


def test_unavailable_delivery_survives_restart_and_replays_once(tmp_path):
    store_path = tmp_path / "notification.sqlite3"
    event = registration_event()
    body = json.dumps(event).encode()
    properties = pika.BasicProperties(
        message_id=event["eventId"],
        correlation_id=event["correlationId"],
    )
    settings = WorkerSettings(
        available=False,
        rabbitmq_host="rabbitmq",
        rabbitmq_port=5672,
        rabbitmq_user="synthetic",
        rabbitmq_password="synthetic-only",
        store_path=store_path,
        retry_initial_seconds=2,
        retry_max_seconds=8,
    )
    unavailable_store = NotificationStore(store_path)
    unavailable_processor = NotificationProcessor(
        NotificationSettings(available=False),
        unavailable_store,
    )
    channel = DeliveryChannel()

    handle_message(
        channel,
        SimpleNamespace(delivery_tag=1),
        properties,
        body,
        unavailable_processor,
        settings,
    )

    assert channel.acknowledged == []
    assert len(channel.connection.callbacks) == 1
    delay, retry_callback = channel.connection.callbacks[0]
    assert delay == 2
    retry_callback()
    assert channel.requeued == [(1, True)]

    recovered_store = NotificationStore(store_path)
    recovered_processor = NotificationProcessor(
        NotificationSettings(available=True),
        recovered_store,
    )
    recovered_channel = DeliveryChannel()
    recovered_settings = WorkerSettings(
        available=True,
        rabbitmq_host="rabbitmq",
        rabbitmq_port=5672,
        rabbitmq_user="synthetic",
        rabbitmq_password="synthetic-only",
        store_path=store_path,
        retry_initial_seconds=2,
        retry_max_seconds=8,
    )

    handle_message(
        recovered_channel,
        SimpleNamespace(delivery_tag=2),
        properties,
        body,
        recovered_processor,
        recovered_settings,
    )
    handle_message(
        recovered_channel,
        SimpleNamespace(delivery_tag=3),
        properties,
        body,
        recovered_processor,
        recovered_settings,
    )

    assert recovered_channel.acknowledged == [2, 3]
    assert recovered_store.count_deliveries() == 1
    assert recovered_store.get_inbox(event["eventId"]).attempts == 2
