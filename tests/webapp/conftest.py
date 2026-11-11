from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import re
from urllib.parse import urljoin, urlsplit

import pytest


BASE_URL = os.environ.get("DEMO_WEB_BASE_URL", "http://demo-web:8080").rstrip("/")
ARTIFACTS_DIR = Path(os.environ.get("WEBAPP_TEST_ARTIFACTS_DIR", "/tmp/webapp-artifacts"))


@dataclass(frozen=True)
class PreparedRun:
    event_id: str
    paid_activity_id: str
    free_activity_id: str
    paid_checkout_url: str
    free_checkout_url: str
    paid_starts_at: datetime
    paid_ends_at: datetime


@pytest.fixture(scope="session")
def browser():
    playwright = pytest.importorskip(
        "playwright.sync_api", reason="Browser tests require Playwright."
    )
    with playwright.sync_playwright() as manager:
        instance = manager.chromium.launch()
        yield instance
        instance.close()


@pytest.fixture
def browser_errors():
    return []


@pytest.fixture
def browser_context_factory(browser, browser_errors):
    contexts = []

    def create_context(viewport=None):
        context = browser.new_context(
            viewport=viewport or {"width": 1366, "height": 768},
            reduced_motion="reduce",
        )
        page = context.new_page()
        page.on("pageerror", lambda error: browser_errors.append(str(error)))
        page.on(
            "console",
            lambda message: browser_errors.append(message.text)
            if message.type == "error" and not message.text.startswith("Failed to load resource:")
            else None,
        )
        contexts.append(context)
        return context, page

    yield create_context

    for context in contexts:
        context.close()


@pytest.fixture
def browser_page(browser_context_factory):
    return browser_context_factory()[1]


def select_persona(page, label):
    page.goto(f"{BASE_URL}/demo")
    page.get_by_role("button", name=label, exact=True).click()
    page.goto(f"{BASE_URL}/demo")


def prepare_demo(page):
    select_persona(page, "Organizer")
    consent = page.get_by_role(
        "checkbox",
        name="I intend to create a fresh run; previously created backend records will remain.",
    )
    consent.check()
    page.get_by_role("button", name="Prepare a new run", exact=True).click()

    run = page.locator(".demo-run")
    run.wait_for(state="visible", timeout=60_000)
    page.get_by_role("link", name="Open published event", exact=True).wait_for(timeout=60_000)
    event_link = page.get_by_role("link", name="Open published event", exact=True)
    event_id = urlsplit(urljoin(BASE_URL, event_link.get_attribute("href"))).path.rsplit("/", 1)[1]

    paid_card = page.locator(".demo-activity-card").filter(has_text="Paid registration journey")
    free_card = page.locator(".demo-activity-card").filter(has_text="Free waiting-list journey")

    def activity_values(card):
        checkout = card.get_by_role("link", name="Open participant checkout", exact=True)
        checkout_url = urljoin(BASE_URL, checkout.get_attribute("href"))
        activity_id = urlsplit(checkout_url).path.split("/")[-2]
        starts_at = datetime.fromisoformat(
            card.get_attribute("data-starts-at").replace("Z", "+00:00")
        )
        ends_at = datetime.fromisoformat(card.get_attribute("data-ends-at").replace("Z", "+00:00"))
        return activity_id, checkout_url, starts_at, ends_at

    paid_id, paid_url, paid_start, paid_end = activity_values(paid_card)
    free_id, free_url, _, _ = activity_values(free_card)
    return PreparedRun(
        event_id=event_id,
        paid_activity_id=paid_id,
        free_activity_id=free_id,
        paid_checkout_url=paid_url,
        free_checkout_url=free_url,
        paid_starts_at=paid_start,
        paid_ends_at=paid_end,
    )


def wait_for_registration(page, expected_status, *, timeout=60_000):
    status = page.locator("[data-registration-status]")
    status.wait_for(state="visible", timeout=timeout)
    page.wait_for_function(
        """expected => {
          const element = document.querySelector("[data-registration-status]");
          return element && element.textContent.trim() === expected;
        }""",
        arg=expected_status,
        timeout=timeout,
    )
    match = re.search(r"/registrations/([0-9a-f-]{36})$", urlsplit(page.url).path)
    assert match, f"Expected a registration detail URL, got {page.url!r}"
    return match.group(1)


def wait_until(page, instant):
    while True:
        remaining = (instant - datetime.now(timezone.utc)).total_seconds()
        if remaining <= 0:
            return
        page.wait_for_timeout(min(1000, max(1, int(remaining * 1000))))


def save_screenshot(page, name):
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(ARTIFACTS_DIR / name), full_page=True)


def assert_no_browser_errors(browser_errors):
    assert not browser_errors, "Browser errors:\n" + "\n".join(browser_errors)
