from attendance_service.settings import Settings as AttendanceSettings
from pydantic import ValidationError
import pytest
from reporting_service.settings import Settings as ReportingSettings


VALUES = {
    "DATABASE_URL": "postgresql+psycopg://synthetic:secret@postgres/test",
    "IDENTITY_BASE_URL": "http://identity-mock:8080",
    "INTERNAL_SERVICE_KEY": "synthetic-secret-never-log",
    "HTTP_TIMEOUT_SECONDS": "5",
    "MESSAGE_RETRY_INITIAL_SECONDS": "1",
    "MESSAGE_RETRY_MAX_SECONDS": "60",
    "CERTIFICATE_ELIGIBILITY_POLICY": "CHECK_IN",
    "CERTIFICATE_ISSUANCE_AFTER_EVENT": "true",
}


@pytest.mark.parametrize("settings", [AttendanceSettings, ReportingSettings])
def test_typed_settings_require_explicit_values(settings):
    configured = settings.model_validate(VALUES)
    assert configured.http_timeout_seconds == 5
    assert "synthetic-secret-never-log" not in repr(configured)
    for field in ("DATABASE_URL", "INTERNAL_SERVICE_KEY", "HTTP_TIMEOUT_SECONDS"):
        invalid = dict(VALUES)
        del invalid[field]
        with pytest.raises(ValidationError):
            settings.model_validate(invalid)
    invalid = {**VALUES, "MESSAGE_RETRY_MAX_SECONDS": "0"}
    with pytest.raises(ValidationError) as error:
        settings.model_validate(invalid)
    assert "synthetic-secret-never-log" not in str(error.value)


@pytest.mark.parametrize("value", ["false", "yes", "1", ""])
def test_certificate_policy_cannot_bypass_completion(value):
    with pytest.raises(ValidationError):
        AttendanceSettings.model_validate(
            {**VALUES, "CERTIFICATE_ISSUANCE_AFTER_EVENT": value}
        )
