import re

from fastapi.testclient import TestClient
from starlette.requests import Request

from demo_web.app import create_app
from demo_web.presentation import PAGE_METADATA, templates


def test_home_page_is_a_simulated_sign_in_with_prototype_banner_and_local_assets(
    web_environment,
):
    with TestClient(create_app(environ=web_environment)) as client:
        response = client.get("/", headers={"Accept": "text/html"})
        stylesheet = client.get("/static/app.css")

    assert response.status_code == 200
    assert stylesheet.status_code == 200
    assert ":focus-visible" in stylesheet.text
    assert "EventHub prototype" in response.text
    assert "not production" in response.text
    assert 'href="/static/app.css"' in response.text
    assert PAGE_METADATA["home"].heading in response.text
    for persona in ("organizer", "organizerUece", "organizerAcademicSim", "participant",
                    "participantTwo"):
        assert f'action="/persona/{persona}"' in response.text
    assert 'action="/persona/visitor"' not in response.text
    assert "Implementation status" not in response.text
    assert 'aria-label="Primary"' not in response.text
    assert "Sign out" not in response.text
    assert "https://" not in response.text
    assert "http://" not in response.text


def test_pages_require_sign_in_and_user_stays_selected_until_sign_out(
    web_environment, sign_in
):
    with TestClient(create_app(environ=web_environment)) as client:
        anonymous = [
            client.get(path, follow_redirects=False)
            for path in ("/events", "/registrations", "/organizer", "/reports")
        ]
        selected = sign_in(client, "organizerUece")
        home = client.get("/", follow_redirects=False)
        participant_page = client.get("/events", follow_redirects=False)
        page = client.get("/organizer/events/new", headers={"Accept": "text/html"})
        csrf = re.search(r'action="/logout">\s*<input type="hidden" name="csrf_token" value="([^"]+)"', page.text)
        assert csrf is not None
        forged = client.post(
            "/logout",
            data={"csrf_token": "forged"},
            headers={"Origin": "http://testserver"},
            follow_redirects=False,
        )
        still_signed_in = client.get("/session").json()
        signed_out = client.post(
            "/logout",
            data={"csrf_token": csrf.group(1)},
            headers={"Origin": "http://testserver"},
            follow_redirects=False,
        )
        after = client.get("/session").json()
        protected = client.get("/events", follow_redirects=False)
        login = client.get("/", headers={"Accept": "text/html"})

    assert all(response.status_code == 303 for response in anonymous)
    assert all(response.headers["location"] == "/" for response in anonymous)
    assert selected.headers["location"] == "/organizer"
    assert home.status_code == 303
    assert home.headers["location"] == "/organizer"
    assert participant_page.status_code == 303
    assert participant_page.headers["location"] == "/organizer"
    assert "Signed in as <strong>Organizador — UECE</strong>" in page.text
    assert 'aria-label="Primary"' in page.text
    assert 'href="/reports"' in page.text
    assert 'href="/registrations"' not in page.text
    assert forged.status_code == 403
    assert still_signed_in["persona"] == "organizerUece"
    assert signed_out.status_code == 303
    assert signed_out.headers["location"] == "/"
    assert after["persona"] == "visitor"
    assert after["csrf_token"] != still_signed_in["csrf_token"]
    assert protected.status_code == 303
    assert login.status_code == 200
    assert 'action="/persona/organizer"' in login.text


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
            "/persona/organizer",
            headers={
                "Origin": "http://testserver",
                "X-CSRF-Token": initial["csrf_token"],
            },
            follow_redirects=False,
        )
        response = client.get("/organizer/events/new", headers={"Accept": "text/html"})

    assert selected.status_code == 303
    assert response.status_code == 200
    assert "Organizador — GESAD/UECE" in response.text
    assert "synthetic-organizer" not in response.text
    assert "20000000-0000-4000-8000-000000000001" not in response.text


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
