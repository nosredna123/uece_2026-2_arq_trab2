from datetime import datetime, timezone

import pytest

from tests.webapp.conftest import (
    BASE_URL,
    assert_no_browser_errors,
    create_published_event,
    save_screenshot,
    select_persona,
    wait_for_registration,
    wait_until,
)


playwright_api = pytest.importorskip("playwright.sync_api")
expect = playwright_api.expect


def test_paid_registration_certificate_and_report(browser_page, browser_errors):
    page = browser_page
    page.goto(f"{BASE_URL}/")
    expect(page.get_by_role("button", name="Participante simulado 01", exact=True)).to_be_visible()

    page.set_viewport_size({"width": 1366, "height": 768})
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    save_screenshot(page, "webapp-desktop-projector.png")

    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    save_screenshot(page, "webapp-mobile.png")

    page.set_viewport_size({"width": 1366, "height": 768})
    published = create_published_event(page)

    select_persona(page, "Participante simulado 01")
    page.goto(f"{BASE_URL}/events/{published.event_id}")
    paid_row = page.locator(".activity-row").filter(has_text=published.paid_title)
    paid_row.get_by_role("link", name="Register", exact=True).click()
    page.wait_for_url(published.paid_checkout_url)
    assert "synthetic-participant-001" not in page.content()
    page.get_by_role("button", name="Confirm registration", exact=True).click(
        click_count=2,
        delay=50,
    )

    registration_id = wait_for_registration(page, "CONFIRMED")
    page.reload()
    expect(page.locator("[data-registration-status]")).to_have_text("CONFIRMED")

    page.goto(f"{BASE_URL}/registrations")
    own_registration = page.locator(".registration-row").filter(has_text=published.paid_title)
    expect(own_registration).to_have_count(1)
    expect(own_registration.get_by_role("link")).to_have_attribute(
        "href", f"/registrations/{registration_id}"
    )

    wait_until(page, published.paid_starts_at)
    page.wait_for_timeout(10_000)
    for attempt in range(3):
        page.goto(f"{BASE_URL}/registrations/{registration_id}")
        check_in = page.locator('form[data-action-form="check-in"]')
        check_in.wait_for(state="visible", timeout=60_000)
        check_in.get_by_role("button", name="Check in", exact=True).click()
        if page.get_by_text("REGISTRATION_NOT_CONFIRMED", exact=True).count() == 0:
            expect(page.get_by_text("Checked in at", exact=False)).to_be_visible()
            break
        if attempt == 2:
            pytest.fail("Attendance did not observe the confirmed registration.")
        page.wait_for_timeout(2_000)

    assert datetime.now(timezone.utc) < published.paid_ends_at, (
        "The paid activity ended before the early-issue check."
    )
    page.locator('form[data-action-form="certificate"]').get_by_role(
        "button",
        name="Request certificate",
        exact=True,
    ).click()
    alert = page.get_by_role("alert")
    expect(alert).to_be_visible()
    expect(alert).to_contain_text("Attendance")
    assert "Certificate of participation" not in page.content()

    wait_until(page, published.paid_ends_at)
    page.goto(f"{BASE_URL}/registrations/{registration_id}")
    page.locator('form[data-action-form="certificate"]').get_by_role(
        "button",
        name="Request certificate",
        exact=True,
    ).click()
    expect(page.locator("#certificate-heading")).to_be_visible()
    certificate_id = page.locator(".certificate-facts dd").nth(0).inner_text()
    expect(page.locator(".certificate-facts dd").nth(1)).to_have_text(registration_id)
    page.add_style_tag(content=".certificate-facts dd { visibility: hidden !important; }")
    save_screenshot(page, "webapp-certificate-record.png")
    page.reload()
    expect(page.locator(".certificate-facts dd").nth(0)).to_have_text(certificate_id)
    expect(page.get_by_role("link", name="Back to registration")).to_be_visible()

    page.emulate_media(media="print")
    assert not page.get_by_role("link", name="Back to registration").is_visible()
    page.emulate_media(media="screen")

    select_persona(page, "Organizador — GESAD/UECE")
    page.goto(f"{BASE_URL}/reports")
    report = page.locator(f'[data-report-event="{published.event_id}"]')
    paid_report_row = report.locator("tbody tr").filter(has_text=published.paid_title)
    expect(paid_report_row).to_be_visible(timeout=60_000)
    for _ in range(30):
        cells = paid_report_row.locator("td").all_inner_texts()
        if cells[1] == "1" and cells[4] == "1" and cells[5] == "1":
            break
        page.wait_for_timeout(2_000)
        page.reload()
    assert cells[0] == "10"
    assert cells[1] == "1", cells
    assert cells[4] == "1", cells
    assert cells[5] == "1", cells
    save_screenshot(page, "webapp-organizer-report.png")
    assert_no_browser_errors(browser_errors)
