from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from demo_web.app import create_app
from demo_web.presentation import BADGE_DEFINITIONS, templates


@pytest.mark.parametrize(
    ("kind", "label"),
    [
        ("live_service", "LIVE SERVICE"),
        ("simulated_dependency", "SIMULATED DEPENDENCY"),
        ("demo_presentation", "DEMO PRESENTATION"),
    ],
)
def test_badge_template_uses_central_visible_text_and_icon(kind, label):
    badge = templates.env.get_template("partials/badge.html").render(kind=kind)
    definition = BADGE_DEFINITIONS[kind]

    assert label in badge
    assert definition.icon in badge
    assert 'aria-hidden="true"' in badge
    assert f'data-badge-kind="{kind}"' in badge


def test_sign_in_page_is_labelled_and_signed_in_pages_explain_badges(web_environment, sign_in):
    with TestClient(create_app(environ=web_environment)) as client:
        response = client.get("/", headers={"Accept": "text/html"})
        sign_in(client)
        page = client.get("/scenario-lab", headers={"Accept": "text/html"})

    html = response.text
    assert response.status_code == 200
    assert 'data-section="sign-in" data-provenance="demo_presentation"' in html
    assert "<details" not in html
    assert page.status_code == 200
    assert "<details" in page.text
    assert "<summary>" in page.text
    for definition in BADGE_DEFINITIONS.values():
        assert definition.label in page.text


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
