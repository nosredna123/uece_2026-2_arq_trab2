from copy import deepcopy
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from reporting_service.errors import InvalidEvent


ORGANIZATION_ID = UUID("20000000-0000-0000-0000-000000000001")
OTHER_ORGANIZATION_ID = UUID("20000000-0000-0000-0000-000000000002")
ACTIVITY_ID = UUID("40000000-0000-0000-0000-000000000001")
OTHER_ACTIVITY_ID = UUID("40000000-0000-0000-0000-000000000002")
PARTICIPANT_ID = UUID("30000000-0000-0000-0000-000000000001")
REGISTRATION_ID = UUID("50000000-0000-0000-0000-000000000001")


def event(
    event_type: str,
    producer: str,
    aggregate_id: UUID,
    data: dict,
    *,
    version: int = 1,
) -> dict:
    return {
        "eventId": str(uuid4()),
        "eventType": event_type,
        "schemaVersion": "1.0.0",
        "aggregateId": str(aggregate_id),
        "aggregateVersion": version,
        "correlationId": str(uuid4()),
        "occurredAt": datetime.now(timezone.utc).isoformat(),
        "producer": producer,
        "data": data,
    }


def activity_event(
    activity_id: UUID = ACTIVITY_ID,
    organization_id: UUID = ORGANIZATION_ID,
) -> dict:
    return event(
        "activity.changed",
        "event-management",
        activity_id,
        {
            "activityId": str(activity_id),
            "eventId": str(UUID("70000000-0000-0000-0000-000000000001")),
            "organizationId": str(organization_id),
            "status": "PUBLISHED",
            "startsAt": "2026-01-01T10:00:00Z",
            "endsAt": "2026-01-01T11:00:00Z",
        },
    )


def registration_event(
    registration_id: UUID,
    status: str,
    activity_id: UUID = ACTIVITY_ID,
    organization_id: UUID = ORGANIZATION_ID,
    version: int = 1,
) -> dict:
    data = {
        "registrationId": str(registration_id),
        "activityId": str(activity_id),
        "organizationId": str(organization_id),
        "participantId": str(PARTICIPANT_ID),
        "status": status,
        "refundStatus": "NONE",
        "amountMinor": 2500,
        "currency": "USD",
        "activityStartsAt": "2026-01-01T10:00:00Z",
        "activityEndsAt": "2026-01-01T11:00:00Z",
    }
    if status == "PENDING_PAYMENT":
        data["expiresAt"] = "2026-01-01T10:30:00Z"
    if status == "OFFERED":
        data["offerExpiresAt"] = "2026-01-01T10:30:00Z"
    return event(
        "registration.changed",
        "registration",
        registration_id,
        data,
        version=version,
    )


def billing_event(
    operation_id: UUID,
    kind: str,
    status: str,
    *,
    activity_id: UUID = ACTIVITY_ID,
    organization_id: UUID = ORGANIZATION_ID,
) -> dict:
    return event(
        "billing.result",
        "billing-mock",
        operation_id,
        {
            "operationId": str(operation_id),
            "attemptId": str(uuid4()),
            "kind": kind,
            "registrationId": str(REGISTRATION_ID),
            "activityId": str(activity_id),
            "organizationId": str(organization_id),
            "status": status,
            "amountMinor": 2500,
            "currency": "USD",
        },
    )


def attendance_event(registration_id: UUID = REGISTRATION_ID) -> dict:
    return event(
        "attendance.recorded",
        "attendance-certification",
        registration_id,
        {
            "registrationId": str(registration_id),
            "activityId": str(ACTIVITY_ID),
            "organizationId": str(ORGANIZATION_ID),
            "participantId": str(PARTICIPANT_ID),
            "checkedInAt": "2026-01-01T10:30:00Z",
        },
    )


def certificate_event(registration_id: UUID = REGISTRATION_ID) -> dict:
    return event(
        "certificate.issued",
        "attendance-certification",
        registration_id,
        {
            "certificateId": str(uuid4()),
            "registrationId": str(registration_id),
            "activityId": str(ACTIVITY_ID),
            "organizationId": str(ORGANIZATION_ID),
            "participantId": str(PARTICIPANT_ID),
            "issuedAt": "2026-01-01T11:30:00Z",
        },
    )


def apply(reporting, *events: dict) -> None:
    for source_event in events:
        reporting.app.state.projector.apply_event(source_event)


def reports(reporting, organization_id=ORGANIZATION_ID, token="organizer-token", **params):
    return reporting.client.get(
        f"/organizations/{organization_id}/reports",
        headers={"Authorization": f"Bearer {token}"},
        params=params,
    )


def test_generated_openapi_matches_reporting_contract(reporting):
    operation = reporting.app.openapi()["paths"][
        "/organizations/{organizationId}/reports"
    ]["get"]
    assert operation["operationId"] == "getOrganizationReports"
    assert operation["parameters"]
    parameters = {parameter["name"]: parameter for parameter in operation["parameters"]}
    assert {"activityId", "limit", "offset", "X-Correlation-ID"} <= parameters.keys()
    assert parameters["limit"]["schema"]["default"] == 20
    assert parameters["limit"]["schema"]["minimum"] == 1
    assert parameters["limit"]["schema"]["maximum"] == 100
    assert parameters["offset"]["schema"]["default"] == 0
    assert operation["responses"]["200"]["content"]["application/json"]["schema"][
        "type"
    ] == "array"


def test_eventual_projection_counts_and_deduplicates_events_and_operations(reporting):
    source_events = [
        activity_event(),
        registration_event(REGISTRATION_ID, "CONFIRMED"),
        registration_event(UUID("50000000-0000-0000-0000-000000000002"), "PENDING_PAYMENT"),
        registration_event(UUID("50000000-0000-0000-0000-000000000003"), "OFFERED"),
        registration_event(UUID("50000000-0000-0000-0000-000000000004"), "WAITLISTED"),
        billing_event(UUID("80000000-0000-0000-0000-000000000001"), "PAYMENT", "APPROVED"),
        billing_event(UUID("80000000-0000-0000-0000-000000000002"), "PAYMENT", "REFUSED"),
        billing_event(UUID("80000000-0000-0000-0000-000000000003"), "REFUND", "APPROVED"),
        attendance_event(),
        certificate_event(),
    ]
    apply(reporting, *source_events)
    duplicate_delivery = deepcopy(source_events[-4])
    apply(reporting, duplicate_delivery)
    repeated_operation = billing_event(
        UUID("80000000-0000-0000-0000-000000000001"),
        "PAYMENT",
        "APPROVED",
    )
    apply(reporting, repeated_operation)

    response = reports(reporting)
    assert response.status_code == 200
    report = response.json()[0]
    assert report["organizationId"] == str(ORGANIZATION_ID)
    assert report["activityId"] == str(ACTIVITY_ID)
    assert report["confirmed"] == 1
    assert report["pendingPayment"] == 1
    assert report["offered"] == 1
    assert report["waiting"] == 1
    assert report["occupied"] == 3
    assert report["checkedIn"] == 1
    assert report["certificates"] == 1
    assert report["paymentsApproved"] == 1
    assert report["paymentsRefused"] == 1
    assert report["refundsApproved"] == 1
    assert report["projectedAt"]


def test_projection_counts_negative_outcomes_and_no_shows(reporting):
    absent = UUID("50000000-0000-0000-0000-000000000009")
    apply(
        reporting,
        activity_event(),
        registration_event(REGISTRATION_ID, "CONFIRMED"),
        registration_event(absent, "CONFIRMED"),
        registration_event(UUID("50000000-0000-0000-0000-000000000005"), "CANCELLED"),
        registration_event(UUID("50000000-0000-0000-0000-000000000006"), "EXPIRED"),
        registration_event(UUID("50000000-0000-0000-0000-000000000007"), "REFUSED"),
        attendance_event(),
    )

    report = reports(reporting).json()[0]

    assert report["cancelled"] == 1
    assert report["expired"] == 1
    assert report["refused"] == 1
    assert report["noShows"] == 1


def test_reports_are_empty_until_events_are_projected(reporting):
    response = reports(reporting)
    assert response.status_code == 200
    assert response.json() == []

    apply(reporting, activity_event())
    response = reports(reporting)
    assert response.status_code == 200
    assert response.json()[0]["occupied"] == 0


def test_stale_registration_snapshot_does_not_replace_newer_state(reporting):
    registration_id = REGISTRATION_ID
    apply(
        reporting,
        activity_event(),
        registration_event(registration_id, "CONFIRMED", version=2),
        registration_event(registration_id, "PENDING_PAYMENT", version=1),
    )

    report = reports(reporting).json()[0]
    assert report["confirmed"] == 1
    assert report["pendingPayment"] == 0


def test_reused_event_id_with_different_content_is_rejected(reporting):
    original = activity_event()
    conflicting = deepcopy(original)
    conflicting["data"]["status"] = "CANCELLED"
    apply(reporting, original)

    with pytest.raises(InvalidEvent, match="different payload"):
        reporting.app.state.projector.apply_event(conflicting)

    assert len(reports(reporting).json()) == 1


def test_organizer_is_restricted_to_own_organization_and_global_admin_can_query_any(
    reporting,
):
    apply(reporting, activity_event(organization_id=OTHER_ORGANIZATION_ID))
    own = reports(reporting)
    foreign = reports(reporting, organization_id=OTHER_ORGANIZATION_ID)
    admin = reports(
        reporting,
        organization_id=OTHER_ORGANIZATION_ID,
        token="admin-token",
    )

    assert own.status_code == 200
    assert own.json() == []
    assert foreign.status_code == 403
    assert admin.status_code == 200
    assert len(admin.json()) == 1


def test_participant_cannot_query_reports(reporting):
    response = reports(reporting, token="participant-token")
    assert response.status_code == 403


def test_activity_filter_order_and_pagination(reporting):
    third_activity = UUID("40000000-0000-0000-0000-000000000003")
    apply(
        reporting,
        activity_event(OTHER_ACTIVITY_ID),
        activity_event(third_activity),
        activity_event(ACTIVITY_ID),
    )

    page = reports(reporting, limit=1, offset=1)
    filtered = reports(reporting, activityId=str(OTHER_ACTIVITY_ID))
    assert page.status_code == 200
    assert len(page.json()) == 1
    assert page.json()[0]["activityId"] == str(OTHER_ACTIVITY_ID)
    assert filtered.status_code == 200
    assert [row["activityId"] for row in filtered.json()] == [str(OTHER_ACTIVITY_ID)]


def test_query_uses_reporting_projection_without_upstream_data_sources(
    reporting, monkeypatch
):
    apply(reporting, activity_event())

    def upstream_unavailable(*args, **kwargs):
        raise AssertionError("Reporting must not request upstream service data")

    monkeypatch.setattr("urllib.request.urlopen", upstream_unavailable)
    response = reports(reporting)

    assert response.status_code == 200
    assert len(response.json()) == 1
    assert reporting.identity.calls[-1][0] == "organizer-token"


def test_correlation_id_is_returned_and_logged_for_report_queries(reporting, caplog):
    correlation_id = "90000000-0000-0000-0000-000000000001"
    response = reporting.client.get(
        f"/organizations/{ORGANIZATION_ID}/reports",
        headers={
            "Authorization": "Bearer organizer-token",
            "X-Correlation-ID": correlation_id,
        },
    )
    assert response.status_code == 200
    assert response.headers["X-Correlation-ID"] == correlation_id
    assert reporting.identity.calls[-1] == ("organizer-token", correlation_id)
    request_log = next(
        record
        for record in caplog.records
        if record.name == "reporting_service.app"
        and record.getMessage() == "http.request.completed"
    )
    assert request_log.correlation_id == correlation_id
    assert request_log.status_code == 200
