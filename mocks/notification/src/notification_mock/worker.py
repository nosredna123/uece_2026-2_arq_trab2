import logging
import sqlite3
import time

import pika
from pika.exceptions import AMQPError
from pika.spec import BasicProperties

from notification_mock.models import InvalidNotificationEvent
from notification_mock.processor import NotificationProcessor
from notification_mock.settings import NotificationSettings, WorkerSettings
from notification_mock.store import NotificationStore


LOGGER = logging.getLogger(__name__)
SOURCE_ROUTING_KEYS = (
    "registration.changed",
    "activity.changed",
    "attendance.recorded",
    "certificate.issued",
)


def create_connection(settings: WorkerSettings) -> pika.BlockingConnection:
    credentials = pika.PlainCredentials(
        settings.rabbitmq_user,
        settings.rabbitmq_password,
    )
    parameters = pika.ConnectionParameters(
        host=settings.rabbitmq_host,
        port=settings.rabbitmq_port,
        credentials=credentials,
        heartbeat=60,
        blocked_connection_timeout=30,
    )
    return pika.BlockingConnection(parameters)


def declare_topology(
    channel: pika.adapters.blocking_connection.BlockingChannel,
) -> None:
    channel.exchange_declare(
        exchange="eventhub.v1",
        exchange_type="topic",
        durable=True,
        auto_delete=False,
    )
    channel.queue_declare(queue="notification.v1", durable=True)
    for routing_key in SOURCE_ROUTING_KEYS:
        channel.queue_bind(
            queue="notification.v1",
            exchange="eventhub.v1",
            routing_key=routing_key,
        )
    channel.queue_declare(queue="notification.v1.quarantine", durable=True)
    channel.queue_bind(
        queue="notification.v1.quarantine",
        exchange="eventhub.v1",
        routing_key="notification.v1.quarantine",
    )


def retry_delay(initial_seconds: int, maximum_seconds: int, attempts: int) -> int:
    delay = initial_seconds
    for _ in range(max(0, attempts - 1)):
        if delay >= maximum_seconds:
            return maximum_seconds
        delay = min(delay * 2, maximum_seconds)
    return min(delay, maximum_seconds)


def quarantine_message(
    channel: pika.adapters.blocking_connection.BlockingChannel,
    properties: BasicProperties,
    body: bytes,
    error: InvalidNotificationEvent,
) -> None:
    headers = dict(properties.headers or {})
    headers["errorCode"] = "INVALID_NOTIFICATION_EVENT"
    headers["errorMessage"] = str(error)
    confirmed = channel.basic_publish(
        exchange="eventhub.v1",
        routing_key="notification.v1.quarantine",
        body=body,
        properties=BasicProperties(
            content_type="application/json",
            delivery_mode=2,
            message_id=properties.message_id,
            correlation_id=properties.correlation_id,
            headers=headers,
        ),
        mandatory=True,
    )
    if confirmed is False:
        raise AMQPError("notification quarantine publish was not confirmed")


def handle_message(
    channel: pika.adapters.blocking_connection.BlockingChannel,
    method: pika.spec.Basic.Deliver,
    properties: BasicProperties,
    body: bytes,
    processor: NotificationProcessor,
    settings: WorkerSettings,
) -> None:
    try:
        result = processor.process_message(
            body,
            transport_message_id=properties.message_id or "",
            transport_correlation_id=properties.correlation_id or "",
        )
    except InvalidNotificationEvent as error:
        quarantine_message(channel, properties, body, error)
        channel.basic_ack(delivery_tag=method.delivery_tag)
        LOGGER.warning(
            "Notification event quarantined event_id=%s correlation_id=%s",
            properties.message_id,
            properties.correlation_id,
        )
        return

    if result.status == "RETRYABLE":
        delay = retry_delay(
            settings.retry_initial_seconds,
            settings.retry_max_seconds,
            result.attempt_count,
        )
        LOGGER.warning(
            "Notification unavailable event_id=%s correlation_id=%s "
            "retry_seconds=%s",
            result.event_id,
            result.correlation_id,
            delay,
        )
        channel.connection.call_later(
            delay,
            lambda: channel.basic_nack(
                delivery_tag=method.delivery_tag,
                requeue=True,
            ),
        )
        return

    channel.basic_ack(delivery_tag=method.delivery_tag)
    LOGGER.info(
        "Notification simulated event_id=%s correlation_id=%s",
        result.event_id,
        result.correlation_id,
    )


def run_worker(settings: WorkerSettings) -> None:
    store = NotificationStore(settings.store_path)
    processor = NotificationProcessor(NotificationSettings(settings.available), store)
    retry_backoff = settings.retry_initial_seconds

    while True:
        connection = None
        try:
            connection = create_connection(settings)
            channel = connection.channel()
            declare_topology(channel)
            channel.confirm_delivery()
            channel.basic_qos(prefetch_count=1)
            channel.basic_consume(
                queue="notification.v1",
                on_message_callback=lambda active_channel, method, properties, body: (
                    handle_message(
                        active_channel,
                        method,
                        properties,
                        body,
                        processor,
                        settings,
                    )
                ),
                auto_ack=False,
            )
            LOGGER.info("Notification mock consuming durable source events")
            channel.start_consuming()
            retry_backoff = settings.retry_initial_seconds
        except (AMQPError, OSError, sqlite3.Error) as error:
            LOGGER.error(
                "Notification worker unavailable error_type=%s retry_seconds=%s",
                type(error).__name__,
                retry_backoff,
            )
            if connection is not None and connection.is_open:
                connection.close()
            time.sleep(retry_backoff)
            retry_backoff = min(
                retry_backoff * 2,
                settings.retry_max_seconds,
            )


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    run_worker(WorkerSettings.from_environment())


if __name__ == "__main__":
    main()
