import json
import logging

import pytest

from attendance_service import observability as attendance_observability
from billing_mock import observability as billing_observability
from identity_access_mock import observability as identity_observability
from notification_mock import observability as notification_observability
from reporting_service import observability as reporting_observability


OBSERVABILITY_MODULES = (
    attendance_observability,
    billing_observability,
    identity_observability,
    notification_observability,
    reporting_observability,
)
CORRELATION_ID = "90000000-0000-0000-0000-000000000001"
REGISTRATION_ID = "50000000-0000-0000-0000-000000000001"


@pytest.mark.parametrize("observability", OBSERVABILITY_MODULES)
def test_json_logs_include_correlation_and_registration_context(observability):
    tokens = observability.bind_context(CORRELATION_ID, REGISTRATION_ID)
    record = logging.LogRecord(
        name="eventhub.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="message.processed",
        args=(),
        exc_info=None,
    )
    record.event_id = "a0000000-0000-0000-0000-000000000001"
    record.event_type = "registration.changed"
    try:
        observability.ContextFilter().filter(record)
        entry = json.loads(observability.StructuredFormatter().format(record))
    finally:
        observability.reset_context(tokens)

    assert entry["correlationId"] == CORRELATION_ID
    assert entry["registrationId"] == REGISTRATION_ID
    assert entry["eventId"] == record.event_id
    assert entry["eventType"] == record.event_type
    assert entry["message"] == "message.processed"
