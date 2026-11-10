from datetime import datetime

from attendance_service.errors import ApiError


def require_confirmed_registration(status: str, operation: str) -> None:
    if status != "CONFIRMED":
        message = (
            "Check-in requires a confirmed registration."
            if operation == "check-in"
            else "Certificate issuance requires a confirmed registration."
        )
        raise ApiError(422, "REGISTRATION_NOT_CONFIRMED", message)


def require_published_activity(status: str | None, subject: str) -> None:
    if status is None:
        raise ApiError(
            409,
            "ELIGIBILITY_NOT_READY",
            "Activity eligibility facts have not been projected.",
        )
    if status != "PUBLISHED":
        message = (
            "Check-in is unavailable for this activity."
            if subject == "check-in"
            else "Certificates are unavailable for this activity."
        )
        raise ApiError(422, "ACTIVITY_NOT_ELIGIBLE", message)


def require_check_in(checked_in_at: datetime | None) -> None:
    if checked_in_at is None:
        raise ApiError(
            422,
            "CHECK_IN_REQUIRED",
            "A recorded check-in is required for certificate eligibility.",
        )


def require_activity_ended(
    ends_at: datetime | None,
    current_time: datetime,
) -> None:
    if ends_at is None:
        raise ApiError(
            409,
            "ELIGIBILITY_NOT_READY",
            "Activity end time has not been projected.",
        )
    if ends_at > current_time:
        raise ApiError(
            422,
            "ACTIVITY_NOT_ENDED",
            "Certificates are issued only after the activity ends.",
        )
