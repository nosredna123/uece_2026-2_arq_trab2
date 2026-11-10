from datetime import datetime, timezone
from uuid import UUID

from reporting_service.domain import build_activity_report


ORGANIZATION_ID = UUID("20000000-0000-0000-0000-000000000001")
ACTIVITY_ID = UUID("40000000-0000-0000-0000-000000000001")


def snapshot(event_type, data, projected_at):
    return {
        "event_type": event_type,
        "snapshot": {"data": data},
        "projected_at": projected_at,
    }


def test_activity_report_counts_business_states_and_payment_outcomes():
    projected_at = datetime(2030, 1, 1, 12, tzinfo=timezone.utc)
    rows = [
        snapshot("activity.changed", {}, projected_at),
        snapshot("registration.changed", {"status": "CONFIRMED"}, projected_at),
        snapshot("registration.changed", {"status": "PENDING_PAYMENT"}, projected_at),
        snapshot("registration.changed", {"status": "OFFERED"}, projected_at),
        snapshot("registration.changed", {"status": "WAITLISTED"}, projected_at),
        snapshot("registration.changed", {"status": "CANCELLED"}, projected_at),
        snapshot(
            "billing.result",
            {"kind": "PAYMENT", "status": "APPROVED"},
            projected_at,
        ),
        snapshot(
            "billing.result",
            {"kind": "PAYMENT", "status": "REFUSED"},
            projected_at,
        ),
        snapshot(
            "billing.result",
            {"kind": "REFUND", "status": "APPROVED"},
            projected_at,
        ),
        snapshot(
            "billing.result",
            {"kind": "REFUND", "status": "REFUSED"},
            projected_at,
        ),
        snapshot("attendance.recorded", {}, projected_at),
        snapshot("certificate.issued", {}, projected_at),
    ]

    report = build_activity_report(ORGANIZATION_ID, ACTIVITY_ID, rows)

    assert report == {
        "organizationId": str(ORGANIZATION_ID),
        "activityId": str(ACTIVITY_ID),
        "confirmed": 1,
        "pendingPayment": 1,
        "offered": 1,
        "waiting": 1,
        "occupied": 3,
        "checkedIn": 1,
        "certificates": 1,
        "paymentsApproved": 1,
        "paymentsRefused": 1,
        "refundsApproved": 1,
        "projectedAt": projected_at,
    }


def test_activity_report_normalizes_naive_projection_timestamp():
    projected_at = datetime(2030, 1, 1, 12)

    report = build_activity_report(
        ORGANIZATION_ID,
        ACTIVITY_ID,
        [snapshot("activity.changed", {}, projected_at)],
    )

    assert report["projectedAt"] == projected_at.replace(tzinfo=timezone.utc)
    assert report["occupied"] == 0
    assert report["paymentsApproved"] == 0
