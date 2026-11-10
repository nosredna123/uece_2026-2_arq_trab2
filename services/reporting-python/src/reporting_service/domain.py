from datetime import timezone
from typing import Any, Mapping
from uuid import UUID


def build_activity_report(
    organization_id: UUID,
    activity_id: UUID,
    snapshots: list[Mapping[str, Any]],
) -> dict[str, Any]:
    data = [row["snapshot"]["data"] for row in snapshots]
    registrations = [
        item
        for row, item in zip(snapshots, data)
        if row["event_type"] == "registration.changed"
    ]
    billing = [
        item
        for row, item in zip(snapshots, data)
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

    return {
        "organizationId": str(organization_id),
        "activityId": str(activity_id),
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
