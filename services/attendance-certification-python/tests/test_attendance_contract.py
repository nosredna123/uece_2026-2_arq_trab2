from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text

from attendance_service.database import message_outbox
from attendance_service.errors import InvalidEvent


ACTIVITY_ID = UUID("40000000-0000-0000-0000-000000000001")
ORGANIZATION_ID = UUID("20000000-0000-0000-0000-000000000001")
PARTICIPANT_ID = UUID("30000000-0000-0000-0000-000000000001")
REGISTRATION_ID = UUID("50000000-0000-0000-0000-000000000001")


def registration_event(status="CONFIRMED", version=1, activity_ends_at=None):
    activity_ends_at = activity_ends_at or (
        datetime.now(UTC) - timedelta(minutes=1)
    )
    return {
        "eventId": str(uuid4()),
        "eventType": "registration.changed",
        "schemaVersion": "1.0.0",
        "aggregateId": str(REGISTRATION_ID),
        "aggregateVersion": version,
        "correlationId": str(uuid4()),
        "occurredAt": datetime.now(UTC).isoformat(),
        "producer": "registration",
        "data": {
            "registrationId": str(REGISTRATION_ID),
            "activityId": str(ACTIVITY_ID),
            "organizationId": str(ORGANIZATION_ID),
            "participantId": str(PARTICIPANT_ID),
            "status": status,
            "refundStatus": "NONE",
            "amountMinor": 0,
            "currency": "USD",
            "activityStartsAt": (
                datetime.now(UTC) - timedelta(hours=2)
            ).isoformat(),
            "activityEndsAt": activity_ends_at.isoformat(),
            **(
                {"expiresAt": (datetime.now(UTC) + timedelta(minutes=10)).isoformat()}
                if status == "PENDING_PAYMENT"
                else {}
            ),
        },
    }


def activity_event(ends_at=None, version=1):
    ends_at = ends_at or datetime.now(UTC) - timedelta(minutes=1)
    return {
        "eventId": str(uuid4()),
        "eventType": "activity.changed",
        "schemaVersion": "1.0.0",
        "aggregateId": str(ACTIVITY_ID),
        "aggregateVersion": version,
        "correlationId": str(uuid4()),
        "occurredAt": datetime.now(UTC).isoformat(),
        "producer": "event-management",
        "data": {
            "activityId": str(ACTIVITY_ID),
            "eventId": str(UUID("70000000-0000-0000-0000-000000000001")),
            "organizationId": str(ORGANIZATION_ID),
            "status": "PUBLISHED",
            "startsAt": (datetime.now(UTC) - timedelta(hours=2)).isoformat(),
            "endsAt": ends_at.isoformat(),
        },
    }


def project_eligibility_facts(attendance, activity_ends_at=None):
    repository = attendance.app.state.repository
    repository.apply_event(activity_event(ends_at=activity_ends_at))
    repository.apply_event(
        registration_event(activity_ends_at=activity_ends_at)
    )


def participant_headers(
    token="participant-token",
    idempotency_key="attendance-test",
):
    return {
        "Authorization": f"Bearer {token}",
        "Idempotency-Key": idempotency_key,
    }


def test_openapi_exposes_check_in_and_certificate_contracts(attendance):
    schema = attendance.client.get("/openapi.json").json()

    check_in = schema["paths"]["/registrations/{id}/check-in"]["post"]
    certificate = schema["paths"]["/registrations/{id}/certificate"]
    assert check_in["operationId"] == "checkInOwnRegistration"
    assert {
        parameter["name"] for parameter in check_in["parameters"]
    } >= {"Idempotency-Key", "X-Correlation-ID"}
    assert certificate["post"]["operationId"] == "issueOwnCertificate"
    assert certificate["get"]["operationId"] == "getOwnCertificate"
    assert "BearerAuth" in schema["components"]["securitySchemes"]
    check_in_response = check_in["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    check_in_schema = check_in_response["$ref"].rsplit("/", 1)[-1]
    assert schema["components"]["schemas"][check_in_schema]["required"] == [
        "registrationId",
        "participantId",
        "checkedInAt",
    ]
    assert "default" in check_in["responses"]
    assert schema["components"]["schemas"]["ErrorResponse"]["required"] == [
        "code",
        "message",
        "correlationId",
    ]
    certificate_schema_ref = certificate["post"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]["$ref"]
    certificate_schema = certificate_schema_ref.rsplit("/", 1)[-1]
    assert schema["components"]["schemas"][certificate_schema]["required"] == [
        "id",
        "registrationId",
        "participantId",
        "activityId",
        "issuedAt",
    ]


def test_check_in_is_idempotent_for_confirmed_registration(attendance):
    project_eligibility_facts(attendance)
    path = f"/registrations/{REGISTRATION_ID}/check-in"

    first = attendance.client.post(path, headers=participant_headers())
    retry = attendance.client.post(path, headers=participant_headers())

    assert first.status_code == 200
    assert retry.status_code == 200
    assert first.json() == retry.json()
    assert first.json()["registrationId"] == str(REGISTRATION_ID)
    assert first.json()["participantId"] == str(PARTICIPANT_ID)
    with attendance.engine.connect() as connection:
        assert connection.scalar(
            text(
                "SELECT COUNT(*) FROM message_outbox "
                "WHERE event_type = 'attendance.recorded'"
            )
        ) == 1
        event = connection.scalar(
            select(message_outbox.c.payload).where(
                message_outbox.c.event_type == "attendance.recorded"
            )
        )
    assert event["data"]["registrationId"] == str(REGISTRATION_ID)
    assert event["data"]["participantId"] == str(PARTICIPANT_ID)
    assert event["eventType"] == "attendance.recorded"


def test_check_in_requires_projected_confirmed_registration(attendance):
    missing = attendance.client.post(
        f"/registrations/{REGISTRATION_ID}/check-in",
        headers=participant_headers(),
    )
    assert missing.status_code == 409
    assert missing.json()["code"] == "ELIGIBILITY_NOT_READY"

    repository = attendance.app.state.repository
    repository.apply_event(activity_event())
    repository.apply_event(registration_event(status="PENDING_PAYMENT"))
    pending = attendance.client.post(
        f"/registrations/{REGISTRATION_ID}/check-in",
        headers=participant_headers(),
    )
    assert pending.status_code == 422
    assert pending.json()["code"] == "REGISTRATION_NOT_CONFIRMED"


def test_certificate_requires_check_in_and_activity_end(attendance):
    future_end = datetime.now(UTC) + timedelta(hours=1)
    project_eligibility_facts(attendance, activity_ends_at=future_end)
    path = f"/registrations/{REGISTRATION_ID}/certificate"
    headers = participant_headers()

    without_check_in = attendance.client.post(path, headers=headers)
    assert without_check_in.status_code == 422
    assert without_check_in.json()["code"] == "CHECK_IN_REQUIRED"

    check_in = attendance.client.post(
        f"/registrations/{REGISTRATION_ID}/check-in",
        headers=headers,
    )
    assert check_in.status_code == 200
    before_end = attendance.client.post(path, headers=headers)
    assert before_end.status_code == 422
    assert before_end.json()["code"] == "ACTIVITY_NOT_ENDED"

    past_end = datetime.now(UTC) - timedelta(minutes=1)
    attendance.app.state.repository.apply_event(
        activity_event(ends_at=past_end, version=2)
    )
    issued = attendance.client.post(path, headers=headers)
    repeated = attendance.client.post(path, headers=headers)

    assert issued.status_code == 200
    assert repeated.status_code == 200
    assert issued.json() == repeated.json()
    assert issued.json()["registrationId"] == str(REGISTRATION_ID)
    assert issued.json()["participantId"] == str(PARTICIPANT_ID)
    assert issued.json()["activityId"] == str(ACTIVITY_ID)
    retrieved = attendance.client.get(path, headers=headers)
    assert retrieved.status_code == 200
    assert retrieved.json() == issued.json()
    with attendance.engine.connect() as connection:
        assert connection.scalar(
            text(
                "SELECT COUNT(*) FROM message_outbox "
                "WHERE event_type = 'certificate.issued'"
            )
        ) == 1
        event = connection.scalar(
            select(message_outbox.c.payload).where(
                message_outbox.c.event_type == "certificate.issued"
            )
        )
    assert event["data"]["certificateId"] == issued.json()["id"]


def test_foreign_participant_cannot_check_in_or_retrieve_certificate(attendance):
    project_eligibility_facts(attendance)
    check_in = attendance.client.post(
        f"/registrations/{REGISTRATION_ID}/check-in",
        headers=participant_headers(
            token="other-participant-token",
            idempotency_key="foreign-check-in",
        ),
    )
    certificate = attendance.client.get(
        f"/registrations/{REGISTRATION_ID}/certificate",
        headers=participant_headers(
            token="other-participant-token",
            idempotency_key="foreign-certificate",
        ),
    )

    assert check_in.status_code == 404
    assert certificate.status_code == 404


def test_non_participant_cannot_check_in(attendance):
    project_eligibility_facts(attendance)
    response = attendance.client.post(
        f"/registrations/{REGISTRATION_ID}/check-in",
        headers=participant_headers(
            token="organizer-token",
            idempotency_key="organizer-check-in",
        ),
    )
    assert response.status_code == 403


def test_event_inbox_deduplicates_and_rejects_conflicting_identity(attendance):
    repository = attendance.app.state.repository
    event = activity_event()

    assert repository.apply_event(event) is True
    assert repository.apply_event(event) is False
    conflicting = {**event, "data": {**event["data"], "status": "CANCELLED"}}
    with pytest.raises(InvalidEvent):
        repository.apply_event(conflicting)


def test_registration_event_projection_uses_latest_version(attendance):
    repository = attendance.app.state.repository
    repository.apply_event(activity_event())
    repository.apply_event(registration_event(version=3, status="CONFIRMED"))
    repository.apply_event(registration_event(version=2, status="PENDING_PAYMENT"))

    response = attendance.client.post(
        f"/registrations/{REGISTRATION_ID}/check-in",
        headers=participant_headers(),
    )

    assert response.status_code == 200
