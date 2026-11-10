from datetime import datetime, timezone
import hashlib
import json
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.engine import Engine

from reporting_service.database import inbox, projection
from reporting_service.errors import InvalidEvent
from reporting_service.events import parse_event


class ReportingRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def apply_event(self, payload: bytes | str | dict) -> bool:
        event = parse_event(payload)
        encoded = json.dumps(event, sort_keys=True, separators=(",", ":")).encode()
        payload_hash = hashlib.sha256(encoded).hexdigest()
        now = datetime.now(timezone.utc)
        data = event["data"]
        event_type = event["eventType"]
        if event_type == "activity.changed":
            organization_id = UUID(data["organizationId"])
            activity_id = UUID(data["activityId"])
        else:
            organization_id = UUID(data["organizationId"])
            activity_id = UUID(data["activityId"])

        with self.engine.begin() as connection:
            existing_inbox = connection.execute(
                select(inbox.c.payload_hash).where(
                    inbox.c.event_id == UUID(event["eventId"])
                )
            ).scalar_one_or_none()
            if existing_inbox is not None:
                if existing_inbox != payload_hash:
                    raise InvalidEvent("eventId was reused with a different payload")
                return False

            existing_projection = connection.execute(
                select(projection.c.version).where(
                    projection.c.producer == event["producer"],
                    projection.c.aggregate_id == UUID(event["aggregateId"]),
                    projection.c.event_type == event_type,
                )
            ).scalar_one_or_none()

            connection.execute(
                inbox.insert().values(
                    event_id=UUID(event["eventId"]),
                    payload_hash=payload_hash,
                    processed_at=now,
                )
            )
            if existing_projection is not None:
                if event["aggregateVersion"] <= existing_projection:
                    return False
                connection.execute(
                    projection.update()
                    .where(
                        projection.c.producer == event["producer"],
                        projection.c.aggregate_id == UUID(event["aggregateId"]),
                        projection.c.event_type == event_type,
                    )
                    .values(
                        version=event["aggregateVersion"],
                        organization_id=organization_id,
                        activity_id=activity_id,
                        snapshot=event,
                        projected_at=now,
                    )
                )
            else:
                connection.execute(
                    projection.insert().values(
                        producer=event["producer"],
                        aggregate_id=UUID(event["aggregateId"]),
                        event_type=event_type,
                        version=event["aggregateVersion"],
                        organization_id=organization_id,
                        activity_id=activity_id,
                        snapshot=event,
                        projected_at=now,
                    )
                )
        return True

    def get_reports(
        self,
        organization_id: UUID,
        activity_id: UUID | None,
        limit: int,
        offset: int,
    ) -> list[dict]:
        activity_conditions = [
            projection.c.organization_id == organization_id,
            projection.c.event_type == "activity.changed",
        ]
        if activity_id is not None:
            activity_conditions.append(projection.c.activity_id == activity_id)
        with self.engine.connect() as connection:
            activity_ids = connection.execute(
                select(projection.c.activity_id)
                .where(*activity_conditions)
                .order_by(projection.c.activity_id)
                .limit(limit)
                .offset(offset)
            ).scalars().all()
            if not activity_ids:
                return []
            rows = connection.execute(
                select(
                    projection.c.activity_id,
                    projection.c.event_type,
                    projection.c.aggregate_id,
                    projection.c.snapshot,
                    projection.c.projected_at,
                )
                .where(
                    projection.c.organization_id == organization_id,
                    projection.c.activity_id.in_(activity_ids),
                )
                .order_by(projection.c.activity_id, projection.c.event_type)
            ).mappings().all()

        other_rows: dict[UUID, list] = {}
        for row in rows:
            activity_key = row["activity_id"]
            other_rows.setdefault(activity_key, []).append(row)

        result = []
        for activity_key in activity_ids:
            snapshots = other_rows[activity_key]
            data = [row["snapshot"]["data"] for row in snapshots]
            registrations = [
                item for row, item in zip(snapshots, data)
                if row["event_type"] == "registration.changed"
            ]
            billing = [
                item for row, item in zip(snapshots, data)
                if row["event_type"] == "billing.result"
            ]
            checked_in = sum(
                row["event_type"] == "attendance.recorded" for row in snapshots
            )
            certificates = sum(
                row["event_type"] == "certificate.issued" for row in snapshots
            )
            confirmed = sum(item["status"] == "CONFIRMED" for item in registrations)
            pending_payment = sum(
                item["status"] == "PENDING_PAYMENT" for item in registrations
            )
            offered = sum(item["status"] == "OFFERED" for item in registrations)
            waiting = sum(item["status"] == "WAITLISTED" for item in registrations)
            projected_at = max(row["projected_at"] for row in snapshots)
            if projected_at.tzinfo is None:
                projected_at = projected_at.replace(tzinfo=timezone.utc)
            result.append(
                {
                    "organizationId": str(organization_id),
                    "activityId": str(activity_key),
                    "confirmed": confirmed,
                    "pendingPayment": pending_payment,
                    "offered": offered,
                    "waiting": waiting,
                    "occupied": confirmed + pending_payment + offered,
                    "checkedIn": checked_in,
                    "certificates": certificates,
                    "paymentsApproved": sum(
                        item["kind"] == "PAYMENT" and item["status"] == "APPROVED"
                        for item in billing
                    ),
                    "paymentsRefused": sum(
                        item["kind"] == "PAYMENT" and item["status"] == "REFUSED"
                        for item in billing
                    ),
                    "refundsApproved": sum(
                        item["kind"] == "REFUND" and item["status"] == "APPROVED"
                        for item in billing
                    ),
                    "projectedAt": projected_at,
                }
            )
        return result
