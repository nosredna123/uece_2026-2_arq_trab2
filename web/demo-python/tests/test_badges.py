import pytest
from fastapi.testclient import TestClient

from demo_web.app import create_app


@pytest.mark.parametrize("path", ["/", "/events", "/registrations", "/organizer/events/new"])
def test_pages_have_no_badges_status_strip_or_label_guide(web_environment, sign_in, path):
    with TestClient(create_app(environ=web_environment)) as client:
        if path != "/":
            sign_in(client, "organizer" if path.startswith("/organizer") else "participant")
        page = client.get(path, headers={"Accept": "text/html"})

    html = page.text
    for marker in (
        "data-badge-kind",
        "data-provenance",
        "provenance-guide",
        "status-strip",
        "LIVE SERVICE",
        "SIMULATED DEPENDENCY",
        "DEMO PRESENTATION",
        "Implementation status",
        "Operational status",
        "How to read the labels",
    ):
        assert marker not in html
