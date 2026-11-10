import re

from fastapi.testclient import TestClient
from starlette.requests import Request

from demo_web.app import create_app
from demo_web.presentation import PAGE_METADATA, templates


def test_home_page_has_prototype_banner_local_assets_and_statuses(web_environment):
    with TestClient(create_app(environ=web_environment)) as client:
        response = client.get("/", headers={"Accept": "text/html"})
        stylesheet = client.get("/static/app.css")

    assert response.status_code == 200
    assert stylesheet.status_code == 200
    assert ":focus-visible" in stylesheet.text
    assert "EventHub prototype" in response.text
    assert "not production" in response.text
    assert 'href="/static/app.css"' in response.text
    assert "Implementation status" in response.text
    assert "Operational status" in response.text
    assert PAGE_METADATA["home"].implementation_status in response.text
    assert "Browser session active; backend services not queried." in response.text
    assert "https://" not in response.text
    assert "http://" not in response.text


def test_html_not_found_page_preserves_status_and_displays_safe_error(web_environment):
    with TestClient(create_app(environ=web_environment)) as client:
        response = client.get(
            "/missing-page",
            headers={"Accept": "text/html"},
        )

    assert response.status_code == 404
    assert "The requested page was not found." in response.text
    assert "Internal Server Error" not in response.text


def test_home_page_does_not_render_persona_credentials(web_environment):
    with TestClient(create_app(environ=web_environment)) as client:
        initial = client.get("/session").json()
        selected = client.post(
            "/persona/participant",
            headers={
                "Origin": "http://testserver",
                "X-CSRF-Token": initial["csrf_token"],
            },
            follow_redirects=False,
        )
        response = client.get("/", headers={"Accept": "text/html"})

    assert selected.status_code == 303
    assert response.status_code == 200
    assert "synthetic-participant" not in response.text
    assert "10000000-0000-4000-8000-000000000001" not in response.text


def test_persona_form_uses_server_rendered_csrf_without_trusting_role_fields(
    web_environment,
):
    with TestClient(create_app(environ=web_environment)) as client:
        page = client.get("/", headers={"Accept": "text/html"})
        csrf = re.search(
            r'name="csrf_token" value="([^"]+)"',
            page.text,
        )
        assert csrf is not None
        response = client.post(
            "/persona/organizer",
            data={"csrf_token": csrf.group(1)},
            headers={"Origin": "http://testserver"},
            follow_redirects=False,
        )
        selected = client.get("/session").json()

    assert response.status_code == 303
    assert 'name="role"' not in page.text
    assert 'name="owner"' not in page.text
    assert selected["persona"] == "organizer"
    assert selected["role"] == "ORGANIZER"


def test_error_template_escapes_untrusted_content(web_environment):
    app = create_app(environ=web_environment)

    @app.get("/_template-escape-check")
    async def render_untrusted_error(request: Request):
        return templates.TemplateResponse(
            request=request,
            name="error.html",
            context={
                "page": PAGE_METADATA["error"],
                "operational_status": "Request failed.",
                "message": '<script>alert("unsafe")</script>',
            },
            status_code=400,
        )

    with TestClient(app) as client:
        response = client.get(
            "/_template-escape-check",
            headers={"Accept": "text/html"},
        )

    assert response.status_code == 400
    assert "<script>" not in response.text
    assert "&lt;script&gt;" in response.text
