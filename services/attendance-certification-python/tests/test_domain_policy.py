from datetime import UTC, datetime, timedelta

import pytest

from attendance_service.domain import (
    require_activity_ended,
    require_check_in,
    require_confirmed_registration,
    require_published_activity,
)
from attendance_service.errors import ApiError


@pytest.mark.parametrize("operation", ["check-in", "certificate"])
def test_confirmed_registration_is_eligible_for_operations(operation):
    require_confirmed_registration("CONFIRMED", operation)


@pytest.mark.parametrize(
    ("operation", "message"),
    [
        ("check-in", "Check-in requires a confirmed registration."),
        ("certificate", "Certificate issuance requires a confirmed registration."),
    ],
)
def test_unconfirmed_registration_is_rejected(operation, message):
    with pytest.raises(ApiError) as error:
        require_confirmed_registration("PENDING_PAYMENT", operation)

    assert error.value.status_code == 422
    assert error.value.code == "REGISTRATION_NOT_CONFIRMED"
    assert str(error.value) == message


@pytest.mark.parametrize(
    ("subject", "message"),
    [
        ("check-in", "Check-in is unavailable for this activity."),
        ("certificate", "Certificates are unavailable for this activity."),
    ],
)
def test_nonpublished_activity_is_rejected(subject, message):
    with pytest.raises(ApiError) as error:
        require_published_activity("CANCELLED", subject)

    assert error.value.status_code == 422
    assert error.value.code == "ACTIVITY_NOT_ELIGIBLE"
    assert str(error.value) == message


@pytest.mark.parametrize("subject", ["check-in", "certificate"])
def test_unprojected_activity_is_not_ready(subject):
    with pytest.raises(ApiError) as error:
        require_published_activity(None, subject)

    assert error.value.status_code == 409
    assert error.value.code == "ELIGIBILITY_NOT_READY"
    assert str(error.value) == "Activity eligibility facts have not been projected."


def test_published_activity_and_existing_check_in_are_eligible():
    require_published_activity("PUBLISHED", "check-in")
    require_check_in(datetime.now(UTC))


def test_missing_check_in_is_rejected():
    with pytest.raises(ApiError) as error:
        require_check_in(None)

    assert error.value.status_code == 422
    assert error.value.code == "CHECK_IN_REQUIRED"
    assert str(error.value) == (
        "A recorded check-in is required for certificate eligibility."
    )


def test_certificate_requires_activity_end_and_accepts_end_boundary():
    now = datetime.now(UTC)
    require_activity_ended(now - timedelta(seconds=1), now)
    require_activity_ended(now, now)

    with pytest.raises(ApiError) as missing_end:
        require_activity_ended(None, now)
    assert missing_end.value.status_code == 409
    assert missing_end.value.code == "ELIGIBILITY_NOT_READY"
    assert str(missing_end.value) == "Activity end time has not been projected."

    with pytest.raises(ApiError) as future_end:
        require_activity_ended(now + timedelta(seconds=1), now)
    assert future_end.value.status_code == 422
    assert future_end.value.code == "ACTIVITY_NOT_ENDED"
    assert str(future_end.value) == (
        "Certificates are issued only after the activity ends."
    )
