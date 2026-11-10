import logging
import threading

import pika
from pika.exceptions import AMQPError
from sqlalchemy.exc import SQLAlchemyError

from reporting_service.errors import InvalidEvent
from reporting_service.events import parse_event
from reporting_service.repository import ReportingRepository
from reporting_service.settings import Settings


LOGGER = logging.getLogger(__name__)
SOURCE_ROUTING_KEYS = (
    "registration.changed",
    "activity.changed",
    "billing.result",
    "attendance.recorded",
    "certificate.issued",
)


class ReportingWorker:
    def __init__(
        self,
        settings: Settings,
        repository: ReportingRepository,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        self.thread = threading.Thread(
            target=self._run,
            name="reporting-rabbitmq",
            daemon=True,
        )
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=5)
            if self.thread.is_alive():
                LOGGER.warning("Reporting message worker did not stop before timeout")

    def _run(self) -> None:
        retry_delay = self.settings.retry_initial_seconds
        while not self.stop_event.is_set():
            connection = None
            try:
                credentials = pika.PlainCredentials(
                    self.settings.rabbitmq_user,
                    self.settings.rabbitmq_password.get_secret_value(),
                )
                connection = pika.BlockingConnection(
                    pika.ConnectionParameters(
                        host=self.settings.rabbitmq_host,
                        port=self.settings.rabbitmq_port,
                        credentials=credentials,
                        heartbeat=60,
                        blocked_connection_timeout=30,
                    )
                )
                channel = connection.channel()
                self._declare_topology(channel)
                channel.confirm_delivery()
                channel.basic_qos(prefetch_count=1)
                channel.basic_consume(
                    queue="reporting.v1",
                    on_message_callback=self._handle_message,
                    auto_ack=False,
                )
                retry_delay = self.settings.retry_initial_seconds
                LOGGER.info("Reporting worker consuming source events")
                while connection.is_open and not self.stop_event.is_set():
                    connection.process_data_events(time_limit=1)
                if connection.is_open:
                    connection.close()
            except (AMQPError, OSError, SQLAlchemyError) as error:
                LOGGER.error(
                    "Reporting worker unavailable error_type=%s retry_seconds=%s",
                    type(error).__name__,
                    retry_delay,
                )
                if connection is not None and connection.is_open:
                    try:
                        connection.close()
                    except (AMQPError, OSError) as close_error:
                        LOGGER.warning(
                            "Reporting worker connection close failed error_type=%s",
                            type(close_error).__name__,
                        )
                self.stop_event.wait(retry_delay)
                retry_delay = min(retry_delay * 2, self.settings.retry_max_seconds)

    @staticmethod
    def _declare_topology(
        channel: pika.adapters.blocking_connection.BlockingChannel,
    ) -> None:
        channel.exchange_declare(
            exchange="eventhub.v1",
            exchange_type="topic",
            durable=True,
            auto_delete=False,
        )
        channel.queue_declare(queue="reporting.v1", durable=True)
        for routing_key in SOURCE_ROUTING_KEYS:
            channel.queue_bind(
                queue="reporting.v1",
                exchange="eventhub.v1",
                routing_key=routing_key,
            )
        channel.queue_declare(queue="reporting.v1.quarantine", durable=True)
        channel.queue_bind(
            queue="reporting.v1.quarantine",
            exchange="eventhub.v1",
            routing_key="reporting.v1.quarantine",
        )

    def _handle_message(
        self,
        channel: pika.adapters.blocking_connection.BlockingChannel,
        method: pika.spec.Basic.Deliver,
        properties: pika.spec.BasicProperties,
        body: bytes,
    ) -> None:
        try:
            event = parse_event(body)
            if (
                properties.message_id != event["eventId"]
                or properties.correlation_id != event["correlationId"]
            ):
                raise InvalidEvent("transport identity does not match the event")
            self.repository.apply_event(event)
        except InvalidEvent as error:
            try:
                self._quarantine(channel, properties, body, error)
            except AMQPError:
                channel.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
                return
            LOGGER.warning(
                "Reporting event quarantined event_id=%s correlation_id=%s",
                properties.message_id,
                properties.correlation_id,
            )
        except SQLAlchemyError as error:
            LOGGER.error(
                "Reporting event persistence failed event_id=%s error_type=%s",
                properties.message_id,
                type(error).__name__,
            )
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
            return
        channel.basic_ack(delivery_tag=method.delivery_tag)

    @staticmethod
    def _quarantine(
        channel: pika.adapters.blocking_connection.BlockingChannel,
        properties: pika.spec.BasicProperties,
        body: bytes,
        error: Exception,
    ) -> None:
        headers = dict(properties.headers or {})
        headers["errorCode"] = "INVALID_REPORTING_EVENT"
        headers["errorMessage"] = str(error)
        confirmed = channel.basic_publish(
            exchange="eventhub.v1",
            routing_key="reporting.v1.quarantine",
            body=body,
            properties=pika.BasicProperties(
                content_type="application/json",
                delivery_mode=2,
                message_id=properties.message_id,
                correlation_id=properties.correlation_id,
                headers=headers,
            ),
            mandatory=True,
        )
        if confirmed is False:
            raise AMQPError("Reporting quarantine publish was not confirmed")
