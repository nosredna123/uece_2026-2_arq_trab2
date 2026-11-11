from datetime import datetime, timezone
from urllib.parse import urlsplit

import pytest

from tests.webapp.conftest import (
    BASE_URL,
    assert_no_browser_errors,
    save_screenshot,
    select_persona,
    wait_for_registration,
    wait_until,
)


playwright_api = pytest.importorskip("playwright.sync_api")
expect = playwright_api.expect


def test_paid_registration_certificate_and_projection(browser_page, browser_errors):
    page = browser_page
    page.goto(f"{BASE_URL}/demo")
    expect(page.get_by_text("EventHub prototype", exact=False)).to_be_visible()
    assert page.locator('[data-badge-kind="demo_presentation"]').count() > 0

    page.set_viewport_size({"width": 1366, "height": 768})
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    save_screenshot(page, "webapp-desktop-projector.png")

    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    save_screenshot(page, "webapp-mobile.png")

    page.set_viewport_size({"width": 1366, "height": 768})
    select_persona(page, "Organizador — GESAD/UECE")

    consent = page.get_by_role(
        "checkbox",
        name="Confirmo a criação de novos dados simulados; registros anteriores serão mantidos.",
    )
    consent.focus()
    page.keyboard.press("Space")
    assert consent.is_checked()
    page.keyboard.press("Space")
    assert not consent.is_checked()
    consent.check()
    prepare_button = page.get_by_role("button", name="Prepare a new run", exact=True)
    prepare_button.focus()
    page.keyboard.press("Enter")

    page.locator(".demo-run").wait_for(state="visible", timeout=60_000)
    event_link = page.get_by_role("link", name="Open published event", exact=True)
    event_link.wait_for(timeout=60_000)
    paid_card = page.locator(".demo-activity-card").filter(
        has_text="Oficina de Engenharia de Software"
    )
    checkout_url = BASE_URL + paid_card.get_by_role(
        "link",
        name="Open participant checkout",
        exact=True,
    ).get_attribute("href")
    paid_activity_id = urlsplit(checkout_url).path.split("/")[-2]
    paid_starts_at = datetime.fromisoformat(
        paid_card.get_attribute("data-starts-at").replace("Z", "+00:00")
    )
    paid_ends_at = datetime.fromisoformat(
        paid_card.get_attribute("data-ends-at").replace("Z", "+00:00")
    )

    select_persona(page, "Participante simulado 01")
    page.goto(checkout_url)
    assert page.locator('[data-badge-kind="live_service"]').count() > 0
    assert page.locator('[data-badge-kind="simulated_dependency"]').count() > 0
    assert "synthetic-participant-001" not in page.content()
    page.get_by_role("button", name="Confirm registration", exact=True).click(
        click_count=2,
        delay=50,
    )

    registration_id = wait_for_registration(page, "CONFIRMED")
    page.reload()
    expect(page.locator("[data-registration-status]")).to_have_text("CONFIRMED")

    page.goto(f"{BASE_URL}/registrations")
    own_registration = page.locator(".registration-row").filter(
        has_text=f"Activity {paid_activity_id}"
    )
    expect(own_registration).to_have_count(1)
    expect(own_registration).to_contain_text(registration_id)

    page.goto(f"{BASE_URL}/registrations/{registration_id}")
    wait_until(page, paid_starts_at)
    page.wait_for_timeout(10_000)
    for attempt in range(3):
        page.goto(f"{BASE_URL}/registrations/{registration_id}")
        check_in = page.locator('form[data-action-form="check-in"]')
        check_in.wait_for(state="visible", timeout=60_000)
        check_in.get_by_role("button", name="Check in", exact=True).click()
        if page.get_by_text("REGISTRATION_NOT_CONFIRMED", exact=True).count() == 0:
            expect(
                page.get_by_text("Attendance returned a check-in record", exact=False)
            ).to_be_visible()
            break
        if attempt == 2:
            pytest.fail("Attendance did not observe the confirmed registration.")
        page.wait_for_timeout(2_000)

    assert datetime.now(timezone.utc) < paid_ends_at, (
        "The configured paid window elapsed before the early-issue check."
    )
    page.locator('form[data-action-form="certificate"]').get_by_role(
        "button",
        name="Request certificate",
        exact=True,
    ).click()
    alert = page.get_by_role("alert")
    expect(alert).to_be_visible()
    expect(alert).to_contain_text("Attendance")
    expect(alert).to_contain_text("correlation")
    assert "Certificate of participation" not in page.content()

    wait_until(page, paid_ends_at)
    page.goto(f"{BASE_URL}/registrations/{registration_id}")
    page.locator('form[data-action-form="certificate"]').get_by_role(
        "button",
        name="Request certificate",
        exact=True,
    ).click()
    expect(page.locator("#certificate-heading")).to_be_visible()
    certificate_id = page.locator(".certificate-facts dd").nth(0).inner_text()
    expect(page.locator(".certificate-facts dd").nth(1)).to_have_text(registration_id)
    page.locator(".certificate-activity strong").evaluate(
        "(element) => { element.textContent = 'Synthetic activity (identifier redacted)'; }"
    )
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
    report_row = page.locator("tbody tr").filter(has_text=paid_activity_id)
    expect(report_row).to_be_visible(timeout=60_000)
    expect(page.get_by_role("columnheader", name="projectedAt (UTC)")).to_be_visible()
    expect(report_row).to_contain_text("1 confirmed", timeout=60_000)
    expect(report_row).to_contain_text("1 checked in", timeout=60_000)
    assert_no_browser_errors(browser_errors)
