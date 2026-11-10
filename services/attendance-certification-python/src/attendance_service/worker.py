import json
import logging
import threading

import pika
from pika.exceptions import AMQPError
from sqlalchemy import func, select, update
from sqlalchemy.exc import SQLAlchemyError

from attendance_service.database import message_outbox
from attendance_service.errors import InvalidEvent
from attendance_service.events import parse_event
from attendance_service.repository import AttendanceRepository
from attendance_service.settings import Settings


LOGGER = logging.getLogger(__name__)
SOURCE_ROUTING_KEYS = ("registration.changed", "activity.changed")


class AttendanceWorker:
    def __init__(
        self,
        settings: Settings,
        repository: AttendanceRepository,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        self.thread = threading.Thread(
            target=self._run,
            name="attendance-rabbitmq",
            daemon=True,
        )
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=5)
            if self.thread.is_alive():
                LOGGER.warning("Attendance message worker did not stop before timeout")

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
                    queue="attendance.v1",
                    on_message_callback=self._handle_message,
                    auto_ack=False,
                )
                retry_delay = self.settings.retry_initial_seconds
                LOGGER.info("Attendance worker consuming durable source events")
                while connection.is_open and not self.stop_event.is_set():
                    connection.process_data_events(time_limit=1)
                    self._publish_pending(channel)
                if connection.is_open:
                    connection.close()
            except (AMQPError, OSError, SQLAlchemyError) as error:
                LOGGER.error(
                    "Attendance worker unavailable error_type=%s retry_seconds=%s",
                    type(error).__name__,
                    retry_delay,
                )
                if connection is not None and connection.is_open:
                    try:
                        connection.close()
                    except (AMQPError, OSError) as close_error:
                        LOGGER.warning(
                            "Attendance worker connection close failed error_type=%s",
                            type(close_error).__name__,
                        )
                self.stop_event.wait(retry_delay)
                retry_delay = min(retry_delay * 2, self.settings.retry_max_seconds)

    @staticmethod
    def _declare_topology(channel: pika.adapters.blocking_connection.BlockingChannel) -> None:
        channel.exchange_declare(
            exchange="eventhub.v1",
            exchange_type="topic",
            durable=True,
            auto_delete=False,
        )
        channel.queue_declare(queue="attendance.v1", durable=True)
        for routing_key in SOURCE_ROUTING_KEYS:
            channel.queue_bind(
                queue="attendance.v1",
                exchange="eventhub.v1",
                routing_key=routing_key,
            )
        channel.queue_declare(queue="attendance.v1.quarantine", durable=True)
        channel.queue_bind(
            queue="attendance.v1.quarantine",
            exchange="eventhub.v1",
            routing_key="attendance.v1.quarantine",
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
                properties.message_id != str(event.event_id)
                or properties.correlation_id != str(event.correlation_id)
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
                "Attendance event quarantined event_id=%s correlation_id=%s",
                properties.message_id,
                properties.correlation_id,
            )
        except SQLAlchemyError as error:
            LOGGER.error(
                "Attendance event persistence failed event_id=%s error_type=%s",
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
        error: InvalidEvent,
    ) -> None:
        headers = dict(properties.headers or {})
        headers["errorCode"] = "INVALID_ATTENDANCE_EVENT"
        headers["errorMessage"] = str(error)
        confirmed = channel.basic_publish(
            exchange="eventhub.v1",
            routing_key="attendance.v1.quarantine",
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
            raise AMQPError("Attendance quarantine publish was not confirmed")

    def _publish_pending(
        self,
        channel: pika.adapters.blocking_connection.BlockingChannel,
    ) -> None:
        with self.repository.engine.connect() as connection:
            pending = connection.execute(
                select(message_outbox)
                .where(message_outbox.c.published_at.is_(None))
                .order_by(message_outbox.c.created_at, message_outbox.c.event_id)
                .limit(50)
            ).mappings().all()

        for item in pending:
            try:
                confirmed = channel.basic_publish(
                    exchange="eventhub.v1",
                    routing_key=item["routing_key"],
                    body=json.dumps(
                        item["payload"],
                        separators=(",", ":"),
                        sort_keys=True,
                    ).encode("utf-8"),
                    properties=pika.BasicProperties(
                        content_type="application/json",
                        delivery_mode=2,
                        message_id=str(item["event_id"]),
                        correlation_id=str(item["correlation_id"]),
                    ),
                    mandatory=True,
                )
                if confirmed is False:
                    raise AMQPError("Attendance event publish was not confirmed")
            except (AMQPError, OSError) as error:
                with self.repository.engine.begin() as connection:
                    connection.execute(
                        update(message_outbox)
                        .where(message_outbox.c.event_id == item["event_id"])
                        .values(
                            attempts=message_outbox.c.attempts + 1,
                            last_error=type(error).__name__,
                        )
                    )
                raise

            with self.repository.engine.begin() as connection:
                connection.execute(
                    update(message_outbox)
                    .where(message_outbox.c.event_id == item["event_id"])
                    .values(
                        published_at=func.current_timestamp(),
                        attempts=message_outbox.c.attempts + 1,
                        last_error=None,
                    )
                )
