import pytest
from fastapi.testclient import TestClient

from demo_web.app import create_app
from demo_web.settings import SettingsError


def test_health_endpoint_starts_with_valid_configuration(web_environment):
    with TestClient(create_app(environ=web_environment)) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"service": "demo-web", "status": "ok"}


@pytest.mark.parametrize(
    ("change", "expected_error"),
    [
        ({"DEMO_WEB_ENABLED": "false"}, "DEMO_WEB_ENABLED"),
        ({"DEMO_WEB_MAX_SESSIONS": "0"}, "DEMO_WEB_MAX_SESSIONS"),
        ({"DEMO_WEB_IDENTITY_FIXTURES_PATH": "/missing/fixtures.json"},
         "DEMO_WEB_IDENTITY_FIXTURES_PATH"),
    ],
)
def test_invalid_configuration_fails_during_startup(
    web_environment, change, expected_error
):
    app = create_app(environ={**web_environment, **change})

    with pytest.raises(SettingsError, match=expected_error):
        with TestClient(app):
            pass
