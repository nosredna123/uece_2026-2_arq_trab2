import json

import pytest


@pytest.fixture
def identity_fixtures(tmp_path):
    path = tmp_path / "identity-fixtures.json"
    path.write_text(
        json.dumps(
            {
                "participant": {
                    "token": "synthetic-participant",
                    "subjectId": "10000000-0000-4000-8000-000000000001",
                    "role": "PARTICIPANT",
                },
                "participantTwo": {
                    "token": "synthetic-participant-two",
                    "subjectId": "10000000-0000-4000-8000-000000000002",
                    "role": "PARTICIPANT",
                },
                "organizer": {
                    "token": "synthetic-organizer",
                    "subjectId": "20000000-0000-4000-8000-000000000001",
                    "role": "ORGANIZER",
                    "organizationId": "30000000-0000-4000-8000-000000000001",
                },
                "globalAdmin": {
                    "token": "synthetic-admin",
                    "subjectId": "40000000-0000-4000-8000-000000000001",
                    "role": "GLOBAL_ADMIN",
                },
            }
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def web_environment(identity_fixtures):
    return {
        "HTTP_TIMEOUT_SECONDS": "5",
        "DEMO_WEB_ENABLED": "true",
        "DEMO_WEB_PORT": "8085",
        "DEMO_WEB_SESSION_TTL_SECONDS": "3600",
        "DEMO_WEB_MAX_SESSIONS": "100",
        "DEMO_WEB_MAX_COMMANDS_PER_SESSION": "20",
        "DEMO_WEB_HISTORY_LIMIT": "20",
        "DEMO_WEB_POLL_INTERVAL_SECONDS": "2",
        "DEMO_WEB_POLL_TIMEOUT_SECONDS": "60",
        "DEMO_WEB_PAID_START_DELAY_SECONDS": "180",
        "DEMO_WEB_ACTIVITY_DURATION_SECONDS": "60",
        "DEMO_WEB_FREE_START_DELAY_SECONDS": "900",
        "DEMO_WEB_EVENT_BASE_URL": "http://event-management:8080",
        "DEMO_WEB_REGISTRATION_BASE_URL": "http://registration:8080",
        "DEMO_WEB_ATTENDANCE_BASE_URL": "http://attendance:8080",
        "DEMO_WEB_REPORTING_BASE_URL": "http://reporting:8080",
        "DEMO_WEB_IDENTITY_FIXTURES_PATH": str(identity_fixtures),
        "IDENTITY_MOCK_SCENARIO_POOL_ENABLED": "false",
        "DEMO_WEB_SCENARIO_POOL_ENABLED": "false",
    }
