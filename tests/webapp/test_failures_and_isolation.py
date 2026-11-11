from datetime import datetime, timedelta, timezone
import os
from uuid import uuid4

import pytest

from tests.webapp.conftest import BASE_URL, select_persona


playwright_api = pytest.importorskip("playwright.sync_api")
expect = playwright_api.expect


def test_missing_identity_invalid_persona_and_stale_form_are_rejected(browser_page):
    page = browser_page
    missing_session = page.goto(f"{BASE_URL}/organizer")
    assert missing_session.status == 200
    assert page.url == f"{BASE_URL}/"
    expect(page.get_by_role("button", name="Sign out", exact=True)).to_have_count(0)

    select_persona(page, "Participante simulado 01")
    page.goto(f"{BASE_URL}/organizer")
    assert page.url == f"{BASE_URL}/events"

    select_persona(page, "Organizador — GESAD/UECE")
    page.goto(f"{BASE_URL}/events")
    assert page.url == f"{BASE_URL}/organizer"
    page.goto(f"{BASE_URL}/organizer/events/new")
    old_csrf = page.locator(
        'form[action="/organizer/events"] input[name="csrf_token"]'
    ).input_value()

    select_persona(page, "Participante simulado 01")
    invalid_persona_csrf = page.locator(
        'form[action="/logout"] input[name="csrf_token"]'
    ).input_value()
    invalid_persona = page.request.post(
        f"{BASE_URL}/persona/not-a-fixture",
        form={"csrf_token": invalid_persona_csrf},
        headers={"Origin": BASE_URL},
    )
    assert invalid_persona.status == 400

    future = datetime.now(timezone.utc) + timedelta(hours=1)
    stale_title = f"stale-browser-form-{uuid4()}"
    stale_form = page.request.post(
        f"{BASE_URL}/organizer/events",
        form={
            "csrf_token": old_csrf,
            "idempotency_key": str(uuid4()),
            "title": stale_title,
            "description": "This stale form must not create data.",
            "starts_at": future.isoformat(),
            "ends_at": (future + timedelta(hours=1)).isoformat(),
        },
        headers={"Origin": BASE_URL},
    )
    assert stale_form.status == 403
    page.goto(f"{BASE_URL}/events?q={stale_title}")
    assert page.locator(".event-row").count() == 0

    event_management = page.request.post(
        "http://event-management:8080/events",
        data={
            "organizationId": "30000000-0000-4000-8000-000000000001",
            "title": f"invalid-credential-{uuid4()}",
            "description": "Invalid credential acceptance probe.",
            "startsAt": future.isoformat(),
            "endsAt": (future + timedelta(hours=1)).isoformat(),
        },
        headers={
            "Authorization": "Bearer invalid-browser-test-credential",
            "Content-Type": "application/json",
            "Idempotency-Key": str(uuid4()),
        },
    )
    assert event_management.status in {401, 403}


@pytest.mark.skipif(
    os.environ.get("WEBAPP_TEST_REPORTING_FAILURE") != "1",
    reason="This probe runs only while the isolated Reporting service is deliberately stopped.",
)
def test_reporting_failure_is_visible_and_not_an_empty_report(browser_page):
    page = browser_page
    select_persona(page, "Organizador — GESAD/UECE")
    response = page.goto(f"{BASE_URL}/reports")
    assert response.status in {502, 503, 504}
    alert = page.get_by_role("alert")
    expect(alert).to_contain_text("Reports could not be loaded")
    expect(alert).to_contain_text("correlation")
    expect(page.locator(".report-table")).to_have_count(0)


@pytest.mark.skipif(
    os.environ.get("WEBAPP_TEST_IDENTITY_FAILURE") != "1",
    reason="This probe runs only while the isolated Identity mock is deliberately stopped.",
)
def test_identity_failure_is_visible_and_does_not_create_an_event(browser_page):
    page = browser_page
    select_persona(page, "Organizador — GESAD/UECE")
    page.goto(f"{BASE_URL}/organizer/events/new")
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    form = page.locator('form[action="/organizer/events"]')
    form.get_by_label("Title", exact=True).fill(f"identity-unavailable-{uuid4()}")
    form.get_by_label("Description", exact=True).fill("Identity failure browser probe.")
    form.get_by_label("Starts at (UTC)", exact=True).fill(future.strftime("%Y-%m-%dT%H:%M"))
    form.get_by_label("Ends at (UTC)", exact=True).fill(
        (future + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M")
    )
    form.get_by_role("button", name="Create event", exact=True).click()
    alert = page.get_by_role("alert")
    expect(alert).to_be_visible()
    assert "/organizer/events/new" in page.url or page.url.endswith("/organizer/events")
