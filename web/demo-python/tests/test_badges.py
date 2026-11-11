from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from demo_web.app import create_app
from demo_web.presentation import templates


@pytest.mark.parametrize("path", ["/", "/events", "/registrations", "/organizer", "/scenario-lab", "/reports"])
def test_pages_have_no_badges_status_strip_or_label_guide(web_environment, sign_in, path):
    with TestClient(create_app(environ=web_environment)) as client:
        if path != "/":
            sign_in(client)
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


def test_request_history_partial_escapes_service_content():
    history = templates.env.get_template("partials/request_history.html").render(
        entries=[
            SimpleNamespace(
                service='<script>alert("x")</script>',
                method="GET",
                status=200,
                elapsed_ms=12,
                correlation_id="12345678-1234-4234-8234-123456789012",
            )
        ]
    )

    assert "<script>" not in history
    assert "&lt;script&gt;" in history
