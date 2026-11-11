import pytest

from tests.webapp.conftest import (
    BASE_URL,
    assert_no_browser_errors,
    prepare_demo,
    select_persona,
    wait_for_registration,
)


playwright_api = pytest.importorskip("playwright.sync_api")
expect = playwright_api.expect


def test_two_person_waitlist_offer_acceptance_and_session_isolation(
    browser_context_factory,
    browser_errors,
):
    organizer_context, organizer = browser_context_factory()
    participant_one_context, participant_one = browser_context_factory()
    participant_two_context, participant_two = browser_context_factory()

    prepared = prepare_demo(organizer)
    participant_one_cookies = participant_one_context.cookies(BASE_URL)
    participant_two_cookies = participant_two_context.cookies(BASE_URL)
    assert not participant_one_cookies
    assert not participant_two_cookies

    select_persona(participant_one, "Participante simulado 01")
    participant_one.goto(prepared.free_checkout_url)
    participant_one.get_by_role("button", name="Confirm registration", exact=True).click()
    first_registration_id = wait_for_registration(participant_one, "CONFIRMED")

    select_persona(participant_two, "Participante simulado 02")
    participant_two.goto(prepared.free_checkout_url)
    participant_two.get_by_role("button", name="Confirm registration", exact=True).click()
    second_registration_id = wait_for_registration(participant_two, "WAITLISTED")

    first_session = {
        cookie["name"]: cookie["value"] for cookie in participant_one_context.cookies(BASE_URL)
    }
    second_session = {
        cookie["name"]: cookie["value"] for cookie in participant_two_context.cookies(BASE_URL)
    }
    assert first_session and second_session
    assert first_session != second_session

    participant_two.goto(f"{BASE_URL}/registrations/{first_registration_id}")
    expect(participant_two.get_by_role("alert")).to_contain_text("not found")
    participant_two.goto(f"{BASE_URL}/registrations/{second_registration_id}")
    expect(participant_two.locator("[data-registration-status]")).to_have_text("WAITLISTED")
    pause_updates = participant_two.get_by_role("button", name="Pause updates", exact=True)
    expect(pause_updates).to_have_attribute("aria-pressed", "false")
    pause_updates.click()
    resume_updates = participant_two.get_by_role("button", name="Resume updates", exact=True)
    expect(resume_updates).to_have_attribute("aria-pressed", "true")
    resume_updates.click()
    expect(
        participant_two.get_by_role("button", name="Pause updates", exact=True)
    ).to_have_attribute("aria-pressed", "false")

    participant_one.goto(f"{BASE_URL}/registrations/{first_registration_id}")
    participant_one.locator('form[data-action-form="cancel"]').get_by_role(
        "button",
        name="Cancel registration",
        exact=True,
    ).click()
    expect(participant_one.locator("[data-registration-status]")).to_have_text("CANCELLED")

    expect(participant_two.locator("[data-registration-status]")).to_have_text(
        "OFFERED",
        timeout=30_000,
    )
    participant_two.locator('form[data-action-form="accept-offer"]').get_by_role(
        "button",
        name="Accept offer",
        exact=True,
    ).click()
    expect(participant_two.locator("[data-registration-status]")).to_have_text("CONFIRMED")

    participant_two.goto(f"{BASE_URL}/events/{prepared.event_id}")
    activity = participant_two.locator(".activity-row").filter(
        has_text="Oficina de Sistemas Autoadaptativos"
    )
    if activity.count() == 0:
        activity = participant_two.locator(".activity-row").filter(
            has_text="Oficina de Sistemas Autoadaptativos"
        )
    expect(activity).to_contain_text("Available: 0")
    assert "synthetic-participant-001" not in participant_two.content()
    assert_no_browser_errors(browser_errors)
