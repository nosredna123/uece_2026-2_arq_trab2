from datetime import datetime, timezone
import hashlib
import json
import sqlite3
from uuid import UUID, uuid4

import pika
from pika.spec import BasicProperties

from billing_mock.models import BillingRequest, InvalidBillingRequest
from billing_mock.settings import BillingSettings
from billing_mock.store import BillingStore


class PublishNotConfirmed(RuntimeError):
    pass


class BillingProcessor:
    def __init__(self, settings: BillingSettings, store: BillingStore):
        self.settings = settings
        self.store = store

    def process_message(
        self,
        message: bytes,
        *,
        transport_message_id: str | None = None,
        transport_correlation_id: str | None = None,
    ) -> dict[str, object] | None:
        request = BillingRequest.from_message(message)
        self._validate_transport_id(
            transport_message_id,
            request.event_id,
            "message ID",
            "eventId",
        )
        self._validate_transport_id(
            transport_correlation_id,
            request.correlation_id,
            "correlation ID",
            "correlationId",
        )

        normalized = request.model_dump(mode="json", by_alias=True)
        request_fingerprint = self._fingerprint(normalized)
        business_data = request.data.model_dump(
            mode="json",
            by_alias=True,
            exclude={"attempt_id"},
        )
        business_fingerprint = self._fingerprint(business_data)

        with self.store.transaction() as connection:
            attempt = connection.execute(
                "SELECT request_fingerprint, result_json FROM attempts "
                "WHERE attempt_id=?",
                (str(request.data.attempt_id),),
            ).fetchone()
            if attempt is not None:
                if attempt["request_fingerprint"] != request_fingerprint:
                    raise InvalidBillingRequest(
                        "attemptId was reused with a different request"
                    )
                return (
                    json.loads(attempt["result_json"])
                    if attempt["result_json"] is not None
                    else None
                )

            event_owner = connection.execute(
                "SELECT attempt_id FROM attempts WHERE source_event_id=?",
                (str(request.event_id),),
            ).fetchone()
            if event_owner is not None:
                raise InvalidBillingRequest(
                    "eventId was reused for a different attempt"
                )

            operation_id = str(request.data.operation_id)
            operation = connection.execute(
                "SELECT * FROM operations WHERE operation_id=?",
                (operation_id,),
            ).fetchone()
            if operation is not None:
                if operation["business_fingerprint"] != business_fingerprint:
                    raise InvalidBillingRequest(
                        "operationId was reused with different business data"
                    )

            original_amount = None
            if request.data.kind == "REFUND":
                original_amount = self._validate_refund(connection, request)

            if operation is None:
                connection.execute(
                    """
                    INSERT INTO operations (
                        operation_id, kind, business_fingerprint,
                        registration_id, activity_id, organization_id,
                        participant_id, amount_minor, currency,
                        original_payment_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        operation_id,
                        request.data.kind,
                        business_fingerprint,
                        str(request.data.registration_id),
                        str(request.data.activity_id),
                        str(request.data.organization_id),
                        str(request.data.participant_id),
                        request.data.amount_minor,
                        request.data.currency,
                        (
                            str(request.data.original_payment_id)
                            if request.data.original_payment_id is not None
                            else None
                        ),
                    ),
                )
                result_version = 0
                approved = False
                last_request_version = 0
            else:
                result_version = operation["result_version"]
                approved = bool(operation["approved"])
                last_request_version = operation["last_request_version"]

            if request.aggregate_version <= last_request_version:
                raise InvalidBillingRequest(
                    "aggregateVersion must increase for a new attempt"
                )

            if approved:
                status = "APPROVED"
            else:
                scenario = (
                    self.settings.payment_scenario
                    if request.data.kind == "PAYMENT"
                    else self.settings.refund_scenario
                )
                if scenario == "TIMEOUT":
                    connection.execute(
                        """
                        INSERT INTO attempts (
                            attempt_id, operation_id, source_event_id,
                            request_fingerprint, status, result_json
                        ) VALUES (?, ?, ?, ?, 'TIMEOUT', NULL)
                        """,
                        (
                            str(request.data.attempt_id),
                            operation_id,
                            str(request.event_id),
                            request_fingerprint,
                        ),
                    )
                    connection.execute(
                        "UPDATE operations SET last_request_version=? "
                        "WHERE operation_id=?",
                        (request.aggregate_version, operation_id),
                    )
                    return None
                status = scenario

            if request.data.kind == "REFUND":
                amount_minor = (
                    original_amount * self.settings.refund_percentage + 50
                ) // 100
            else:
                amount_minor = request.data.amount_minor

            next_result_version = result_version + 1
            result = self._make_result(
                request,
                status,
                amount_minor,
                next_result_version,
            )
            result_json = json.dumps(
                result,
                sort_keys=True,
                separators=(",", ":"),
            )
            connection.execute(
                """
                INSERT INTO attempts (
                    attempt_id, operation_id, source_event_id,
                    request_fingerprint, status, result_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(request.data.attempt_id),
                    operation_id,
                    str(request.event_id),
                    request_fingerprint,
                    status,
                    result_json,
                ),
            )
            connection.execute(
                """
                UPDATE operations
                SET approved=?, result_version=?, last_request_version=?
                WHERE operation_id=?
                """,
                (
                    1 if status == "APPROVED" else 0,
                    next_result_version,
                    request.aggregate_version,
                    operation_id,
                ),
            )
            connection.execute(
                """
                INSERT INTO outbox (event_id, body, correlation_id)
                VALUES (?, ?, ?)
                """,
                (
                    result["eventId"],
                    result_json,
                    result["correlationId"],
                ),
            )
            return result

    def publish_pending(
        self,
        channel: pika.adapters.blocking_connection.BlockingChannel,
    ) -> int:
        published = 0
        for message in self.store.pending_outbox():
            confirmed = channel.basic_publish(
                exchange="eventhub.v1",
                routing_key="billing.result",
                body=message.body,
                properties=BasicProperties(
                    content_type="application/json",
                    delivery_mode=2,
                    message_id=message.event_id,
                    correlation_id=message.correlation_id,
                ),
                mandatory=True,
            )
            if confirmed is False:
                raise PublishNotConfirmed("RabbitMQ did not confirm Billing result")
            self.store.mark_published(message.event_id)
            published += 1
        return published

    def _validate_refund(
        self,
        connection: sqlite3.Connection,
        request: BillingRequest,
    ) -> int:
        original_payment = connection.execute(
            """
            SELECT * FROM operations
            WHERE operation_id=? AND kind='PAYMENT' AND approved=1
            """,
            (str(request.data.original_payment_id),),
        ).fetchone()
        if original_payment is None:
            raise InvalidBillingRequest(
                "refund requires a successful original payment"
            )

        matches = (
            original_payment["registration_id"]
            == str(request.data.registration_id)
            and original_payment["activity_id"] == str(request.data.activity_id)
            and original_payment["organization_id"]
            == str(request.data.organization_id)
            and original_payment["participant_id"]
            == str(request.data.participant_id)
            and original_payment["currency"] == request.data.currency
            and original_payment["amount_minor"] == request.data.amount_minor
        )
        if not matches:
            raise InvalidBillingRequest(
                "refund does not match the original payment"
            )

        previous_refund = connection.execute(
            """
            SELECT operation_id FROM operations
            WHERE kind='REFUND' AND original_payment_id=? AND approved=1
              AND operation_id<>?
            """,
            (
                str(request.data.original_payment_id),
                str(request.data.operation_id),
            ),
        ).fetchone()
        if previous_refund is not None:
            raise InvalidBillingRequest(
                "original payment already has a successful refund"
            )
        return original_payment["amount_minor"]

    @staticmethod
    def _fingerprint(value: dict[str, object]) -> str:
        canonical = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    @staticmethod
    def _validate_transport_id(
        transport_value: str | None,
        envelope_value: UUID,
        transport_name: str,
        envelope_name: str,
    ) -> None:
        if transport_value is None:
            return
        try:
            matches = UUID(transport_value) == envelope_value
        except ValueError:
            matches = False
        if not matches:
            raise InvalidBillingRequest(
                f"transport {transport_name} does not match {envelope_name}"
            )

    @staticmethod
    def _make_result(
        request: BillingRequest,
        status: str,
        amount_minor: int,
        aggregate_version: int,
    ) -> dict[str, object]:
        return {
            "eventId": str(uuid4()),
            "eventType": "billing.result",
            "schemaVersion": "1.0.0",
            "aggregateId": str(request.data.operation_id),
            "aggregateVersion": aggregate_version,
            "correlationId": str(request.correlation_id),
            "occurredAt": (
                datetime.now(timezone.utc)
                .isoformat(timespec="microseconds")
                .replace("+00:00", "Z")
            ),
            "producer": "billing-mock",
            "data": {
                "operationId": str(request.data.operation_id),
                "attemptId": str(request.data.attempt_id),
                "kind": request.data.kind,
                "registrationId": str(request.data.registration_id),
                "activityId": str(request.data.activity_id),
                "organizationId": str(request.data.organization_id),
                "status": status,
                "amountMinor": amount_minor,
                "currency": request.data.currency,
            },
        }
