from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import re
from urllib.parse import urlsplit
from uuid import uuid4

import pytest


BASE_URL = os.environ.get("DEMO_WEB_BASE_URL", "http://demo-web:8080").rstrip("/")
ARTIFACTS_DIR = Path(os.environ.get("WEBAPP_TEST_ARTIFACTS_DIR", "/tmp/webapp-artifacts"))


@dataclass(frozen=True)
class PublishedEvent:
    event_id: str
    title: str
    paid_title: str
    free_title: str
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
    page.goto(f"{BASE_URL}/")
    sign_out = page.get_by_role("button", name="Sign out", exact=True)
    if sign_out.count():
        sign_out.click()
        page.wait_for_url(f"{BASE_URL}/")
    page.get_by_role("button", name=label, exact=True).click()
    page.wait_for_url(re.compile(r".*/(organizer|events)$"))


def _local(instant):
    return instant.strftime("%Y-%m-%dT%H:%M")


def _fill_fields(form, values):
    for label, value in values.items():
        form.get_by_label(label, exact=True).fill(value)


def create_published_event(page, *, paid_start_minutes=2, free_start_minutes=5):
    """Create and publish an event through the organizer pages (paid + free activity)."""
    select_persona(page, "Organizador — GESAD/UECE")
    suffix = uuid4().hex[:6]
    title = f"[TESTE] Evento navegador {suffix}"
    paid_title = f"[TESTE] Oficina paga {suffix}"
    free_title = f"[TESTE] Oficina gratuita {suffix}"
    base = datetime.now(timezone.utc).replace(second=0, microsecond=0) + timedelta(minutes=1)
    paid_start = base + timedelta(minutes=paid_start_minutes)
    paid_end = paid_start + timedelta(minutes=1)
    free_start = base + timedelta(minutes=free_start_minutes)
    free_end = free_start + timedelta(minutes=1)

    page.goto(f"{BASE_URL}/organizer/events/new")
    form = page.locator('form[action="/organizer/events"]')
    _fill_fields(
        form,
        {
            "Title": title,
            "Description": "Evento criado pelo teste de navegador.",
            "Starts at (UTC)": _local(paid_start - timedelta(minutes=1)),
            "Ends at (UTC)": _local(free_end + timedelta(minutes=1)),
        },
    )
    form.get_by_role("button", name="Create event", exact=True).click()
    page.wait_for_url(re.compile(r".*/organizer/events/[0-9a-f-]{36}\?notice=created$"))
    event_id = urlsplit(page.url).path.rsplit("/", 1)[1]

    for activity_title, start, end, capacity, price in (
        (paid_title, paid_start, paid_end, "10", "25,00"),
        (free_title, free_start, free_end, "1", "0"),
    ):
        form = page.locator('form[data-section="add-activity"]')
        _fill_fields(
            form,
            {
                "Title": activity_title,
                "Starts at (UTC)": _local(start),
                "Ends at (UTC)": _local(end),
                "Location": "Laboratório GESAD",
                "Responsible": "Equipe de testes",
                "Capacity": capacity,
                "Price (R$, 0 = free)": price,
            },
        )
        form.get_by_role("button", name="Add activity", exact=True).click()
        page.wait_for_url(re.compile(r".*\?notice=activity-added$"))

    page.get_by_role("button", name="Publish event", exact=True).click()
    page.wait_for_url(re.compile(r".*\?notice=published$"))

    def activity_id(activity_title):
        action = (
            page.locator(".organizer-activity")
            .filter(has_text=activity_title)
            .locator("form")
            .first.get_attribute("action")
        )
        return action.rsplit("/", 1)[1]

    paid_id = activity_id(paid_title)
    free_id = activity_id(free_title)
    return PublishedEvent(
        event_id=event_id,
        title=title,
        paid_title=paid_title,
        free_title=free_title,
        paid_activity_id=paid_id,
        free_activity_id=free_id,
        paid_checkout_url=f"{BASE_URL}/activities/{paid_id}/checkout",
        free_checkout_url=f"{BASE_URL}/activities/{free_id}/checkout",
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
