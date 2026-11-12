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
        "cancelled": 1,
        "expired": 0,
        "refused": 0,
        "noShows": 0,
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


def test_activity_report_counts_cancellations_expirations_and_refusals():
    projected_at = datetime(2030, 1, 1, 12, tzinfo=timezone.utc)
    rows = [
        snapshot("registration.changed", {"status": status}, projected_at)
        for status in ("CANCELLED", "CANCELLED", "EXPIRED", "REFUSED", "REFUSED", "REFUSED")
    ]

    report = build_activity_report(ORGANIZATION_ID, ACTIVITY_ID, rows)

    assert (report["cancelled"], report["expired"], report["refused"]) == (2, 1, 3)


def no_show_rows(ends_at, activity_status="PUBLISHED"):
    projected_at = datetime(2030, 1, 1, 12, tzinfo=timezone.utc)
    return [
        snapshot(
            "activity.changed",
            {"status": activity_status, "endsAt": ends_at},
            projected_at,
        ),
        *(
            snapshot(
                "registration.changed",
                {"registrationId": f"r{index}", "status": status},
                projected_at,
            )
            for index, status in enumerate(
                ("CONFIRMED", "CONFIRMED", "CONFIRMED", "CANCELLED", "WAITLISTED")
            )
        ),
        snapshot("attendance.recorded", {"registrationId": "r0"}, projected_at),
    ]


def test_activity_report_counts_no_shows_only_after_the_activity_ends():
    now = datetime(2030, 1, 1, 12, tzinfo=timezone.utc)

    ended = build_activity_report(
        ORGANIZATION_ID, ACTIVITY_ID, no_show_rows("2030-01-01T11:59:00Z"), now
    )
    running = build_activity_report(
        ORGANIZATION_ID, ACTIVITY_ID, no_show_rows("2030-01-01T12:30:00Z"), now
    )
    cancelled = build_activity_report(
        ORGANIZATION_ID,
        ACTIVITY_ID,
        no_show_rows("2030-01-01T11:59:00Z", "CANCELLED"),
        now,
    )

    assert ended["noShows"] == 2
    assert running["noShows"] == 0
    assert cancelled["noShows"] == 0
