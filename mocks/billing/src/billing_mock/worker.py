import logging
import sqlite3
import time

import pika
from pika.exceptions import AMQPError
from pika.spec import BasicProperties

from billing_mock.models import BillingRequest, InvalidBillingRequest
from billing_mock.observability import (
    bind_context,
    configure_logging,
    reset_context,
)
from billing_mock.processor import BillingProcessor, PublishNotConfirmed
from billing_mock.settings import WorkerSettings
from billing_mock.store import BillingStore


LOGGER = logging.getLogger(__name__)
# How often delayed payment results are checked for publication.
DELAYED_PUBLISH_POLL_SECONDS = 0.5


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


def declare_topology(channel: pika.adapters.blocking_connection.BlockingChannel) -> None:
    channel.exchange_declare(
        exchange="eventhub.v1",
        exchange_type="topic",
        durable=True,
        auto_delete=False,
    )
    channel.queue_declare(queue="billing.v1", durable=True)
    channel.queue_bind(
        queue="billing.v1",
        exchange="eventhub.v1",
        routing_key="billing.requested",
    )
    channel.queue_declare(queue="billing.v1.quarantine", durable=True)
    channel.queue_bind(
        queue="billing.v1.quarantine",
        exchange="eventhub.v1",
        routing_key="billing.v1.quarantine",
    )


def quarantine_message(
    channel: pika.adapters.blocking_connection.BlockingChannel,
    properties: BasicProperties,
    body: bytes,
    error: InvalidBillingRequest,
) -> None:
    headers = dict(properties.headers or {})
    headers["errorCode"] = "INVALID_BILLING_REQUEST"
    headers["errorMessage"] = str(error)
    channel.basic_publish(
        exchange="eventhub.v1",
        routing_key="billing.v1.quarantine",
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


def run_worker(settings: WorkerSettings) -> None:
    store = BillingStore(settings.store_path)
    processor = BillingProcessor(settings.billing, store)
    retry_delay = settings.retry_initial_seconds

    while True:
        connection = None
        try:
            connection = create_connection(settings)
            channel = connection.channel()
            declare_topology(channel)
            channel.confirm_delivery()
            processor.publish_pending(channel)

            if settings.billing.payment_delay_seconds[1] > 0:
                # Delayed results are published by this timer, so consuming never waits.
                def publish_due(active_connection=connection, active_channel=channel) -> None:
                    processor.publish_pending(active_channel)
                    active_connection.call_later(DELAYED_PUBLISH_POLL_SECONDS, publish_due)

                connection.call_later(DELAYED_PUBLISH_POLL_SECONDS, publish_due)

            def on_message(
                active_channel: pika.adapters.blocking_connection.BlockingChannel,
                method: pika.spec.Basic.Deliver,
                properties: BasicProperties,
                body: bytes,
            ) -> None:
                context_tokens = bind_context(properties.correlation_id)
                try:
                    try:
                        request = BillingRequest.from_message(body)
                        reset_context(context_tokens)
                        context_tokens = bind_context(
                            str(request.correlation_id),
                            str(request.data.registration_id),
                        )
                        processor.process_message(
                            body,
                            transport_message_id=properties.message_id or "",
                            transport_correlation_id=properties.correlation_id or "",
                        )
                    except InvalidBillingRequest as error:
                        quarantine_message(active_channel, properties, body, error)
                        LOGGER.warning(
                            "message.quarantined",
                            extra={
                                "event_id": properties.message_id,
                                "event_type": "billing.requested",
                                "correlation_id": properties.correlation_id,
                            },
                        )
                    else:
                        LOGGER.info(
                            "message.processed",
                            extra={
                                "event_id": str(request.event_id),
                                "event_type": request.event_type,
                                "correlation_id": str(request.correlation_id),
                                "registration_id": str(request.data.registration_id),
                            },
                        )
                        processor.publish_pending(active_channel)
                    active_channel.basic_ack(delivery_tag=method.delivery_tag)
                finally:
                    reset_context(context_tokens)

            channel.basic_qos(prefetch_count=1)
            channel.basic_consume(
                queue="billing.v1",
                on_message_callback=on_message,
                auto_ack=False,
            )
            LOGGER.info("message_worker.ready")
            channel.start_consuming()
            retry_delay = settings.retry_initial_seconds
        except (AMQPError, OSError, sqlite3.Error, PublishNotConfirmed) as error:
            LOGGER.error(
                "message_worker.unavailable",
                extra={
                    "error_type": type(error).__name__,
                    "retry_seconds": retry_delay,
                },
            )
            if connection is not None and connection.is_open:
                connection.close()
            time.sleep(retry_delay)
            retry_delay = min(
                retry_delay * 2,
                settings.retry_max_seconds,
            )


def main() -> None:
    configure_logging()
    run_worker(WorkerSettings.from_environment())


if __name__ == "__main__":
    main()
