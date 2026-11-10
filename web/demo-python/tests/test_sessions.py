import json

from fastapi.testclient import TestClient

from demo_web.app import create_app
from demo_web.sessions import SessionStore


def test_browser_sessions_are_isolated_and_cookie_is_opaque(web_environment):
    with TestClient(create_app(environ=web_environment)) as client:
        first_response = client.get("/session")
        first_cookie = client.cookies.get("demo_session")
        assert first_cookie
        client.cookies.clear()

        second_response = client.get("/session")
        second_cookie = client.cookies.get("demo_session")
        assert second_cookie
        client.cookies.set("demo_session", first_cookie)
        first_initial = client.get("/session").json()
        changed = client.post(
            "/persona/participant",
            headers={
                "Origin": "http://testserver",
                "X-CSRF-Token": first_initial["csrf_token"],
            },
            follow_redirects=False,
        )
        first_state = client.get("/session").json()
        client.cookies.set("demo_session", second_cookie)
        second_state = client.get("/session").json()

    assert first_response.status_code == second_response.status_code == 200
    assert first_cookie and second_cookie and first_cookie != second_cookie
    assert changed.status_code == 303
    assert first_state["persona"] == "participant"
    assert second_state["persona"] == "visitor"
    assert first_state["csrf_token"] and second_state["csrf_token"]
    assert first_state["csrf_token"] != second_state["csrf_token"]
    assert "synthetic-participant" not in first_response.text
    cookie_header = first_response.headers["set-cookie"].lower()
    assert "httponly" in cookie_header
    assert "samesite=lax" in cookie_header


def test_persona_change_requires_csrf_and_same_origin(web_environment):
    with TestClient(create_app(environ=web_environment)) as client:
        initial = client.get("/session").json()
        missing_token = client.post("/persona/participant", headers={"Origin": "http://testserver"})
        foreign_origin = client.post(
            "/persona/participant",
            headers={
                "Origin": "https://attacker.example",
                "X-CSRF-Token": initial["csrf_token"],
            },
        )
        invalid_token = client.post(
            "/persona/participant",
            headers={
                "Origin": "http://testserver",
                "X-CSRF-Token": "not-the-session-token",
            },
        )

        assert missing_token.status_code == 403
        assert foreign_origin.status_code == 403
        assert invalid_token.status_code == 403
        assert client.get("/session").json()["persona"] == "visitor"

        changed = client.post(
            "/persona/participant",
            headers={
                "Origin": "http://testserver",
                "X-CSRF-Token": initial["csrf_token"],
            },
            follow_redirects=False,
        )

        assert changed.status_code == 303
        assert changed.headers["location"] == "/session"
        selected = client.get("/session").json()
        assert selected["persona"] == "participant"
        assert selected["role"] == "PARTICIPANT"
        assert "token" not in selected
        assert selected["csrf_token"] != initial["csrf_token"]


def test_invalid_persona_is_rejected(web_environment):
    with TestClient(create_app(environ=web_environment)) as client:
        state = client.get("/session").json()
        response = client.post(
            "/persona/globalAdmin",
            headers={
                "Origin": "http://testserver",
                "X-CSRF-Token": state["csrf_token"],
            },
        )

    assert response.status_code == 400


def test_session_expiry_capacity_and_restart_invalidation(identity_fixtures):
    fixtures = json.loads(identity_fixtures.read_text(encoding="utf-8"))
    now = [0.0]
    store = SessionStore(
        fixtures,
        ttl_seconds=10,
        max_sessions=1,
        clock=lambda: now[0],
    )
    first = store.create()
    evicted_id = first.id
    second = store.create()

    assert store.get(evicted_id) is None
    assert store.get(second.id) is second

    restarted_store = SessionStore(fixtures, ttl_seconds=10, max_sessions=1)
    assert restarted_store.get(second.id) is None

    now[0] = 11.0
    assert store.get(second.id) is None
