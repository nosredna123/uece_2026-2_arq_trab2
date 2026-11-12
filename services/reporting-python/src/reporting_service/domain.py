from datetime import datetime, timezone
from typing import Any, Mapping
from uuid import UUID


def build_activity_report(
    organization_id: UUID,
    activity_id: UUID,
    snapshots: list[Mapping[str, Any]],
    now: datetime | None = None,
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
    attended = {
        item.get("registrationId")
        for row, item in zip(snapshots, data)
        if row["event_type"] == "attendance.recorded"
    }
    no_shows = 0
    if _activity_ended(snapshots, data, now or datetime.now(timezone.utc)):
        no_shows = sum(
            item["status"] == "CONFIRMED"
            and item.get("registrationId") not in attended
            for item in registrations
        )
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
        "cancelled": sum(item["status"] == "CANCELLED" for item in registrations),
        "expired": sum(item["status"] == "EXPIRED" for item in registrations),
        "refused": sum(item["status"] == "REFUSED" for item in registrations),
        "noShows": no_shows,
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


def _activity_ended(
    snapshots: list[Mapping[str, Any]],
    data: list[Mapping[str, Any]],
    now: datetime,
) -> bool:
    """True once a published activity's latest known end time has passed."""
    for row, item in zip(snapshots, data):
        if row["event_type"] != "activity.changed" or "endsAt" not in item:
            continue
        if item.get("status") == "CANCELLED":
            return False
        ends_at = datetime.fromisoformat(item["endsAt"].replace("Z", "+00:00"))
        if ends_at.tzinfo is None:
            ends_at = ends_at.replace(tzinfo=timezone.utc)
        return ends_at <= now
    return False
