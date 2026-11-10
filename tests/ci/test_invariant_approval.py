from datetime import datetime, timezone

from scripts.require_invariant_approval import (
    has_fresh_architect_approval,
    invariants_changed,
    latest_approvals,
)


def test_only_changes_to_invariant_arrays_require_human_approval():
    assert not invariants_changed(["same"], ["same"])
    assert invariants_changed(["old"], ["new"])
    assert invariants_changed(None, ["new"])
    assert invariants_changed(["old"], None)


def test_author_and_unconfigured_reviewers_cannot_approve():
    reviews = [
        _review("author", "APPROVED"),
        _review("other", "APPROVED"),
    ]

    assert not has_fresh_architect_approval(
        reviews,
        author="author",
        allowed_reviewers={"author"},
        head_commit_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def test_latest_review_must_be_fresh_and_approved_by_configured_architect():
    approval = _review("architect", "APPROVED", "2026-02-01T00:00:00Z")
    assert has_fresh_architect_approval(
        [approval],
        author="contributor",
        allowed_reviewers={"architect"},
        head_commit_time=datetime(2026, 1, 31, tzinfo=timezone.utc),
    )
    assert not has_fresh_architect_approval(
        [approval],
        author="contributor",
        allowed_reviewers={"architect"},
        head_commit_time=datetime(2026, 2, 2, tzinfo=timezone.utc),
    )


def test_newer_changes_requested_review_invalidates_earlier_approval():
    reviews = [
        _review("architect", "APPROVED", "2026-02-01T00:00:00Z"),
        _review("architect", "CHANGES_REQUESTED", "2026-02-02T00:00:00Z"),
    ]

    assert latest_approvals(reviews)["architect"][1] == "CHANGES_REQUESTED"
    assert not has_fresh_architect_approval(
        reviews,
        author="contributor",
        allowed_reviewers={"architect"},
        head_commit_time=datetime(2026, 1, 31, tzinfo=timezone.utc),
    )


def test_bot_review_is_not_human_approval():
    review = _review("architect[bot]", "APPROVED")
    review["user"]["type"] = "Bot"

    assert not has_fresh_architect_approval(
        [review],
        author="contributor",
        allowed_reviewers={"architect[bot]"},
        head_commit_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def _review(login, state, submitted_at="2026-01-02T00:00:00Z"):
    return {
        "user": {"login": login, "type": "User"},
        "state": state,
        "submitted_at": submitted_at,
    }
