#!/usr/bin/env python3
"""Require a configured architect's fresh approval when rationale invariants change."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]


def _git(*arguments):
    return subprocess.run(
        ["git", *arguments],
        check=True,
        capture_output=True,
        text=True,
        cwd=ROOT,
    ).stdout.strip()


def changed_invariant_files(base, head):
    paths = _git(
        "diff",
        "--name-only",
        "--diff-filter=ACMRTD",
        f"{base}...{head}",
        "--",
        "docs/rationales/ADR-*.json",
    ).splitlines()
    changed = []
    for path in paths:
        try:
            old = json.loads(_git("show", f"{base}:{path}"))["invariants"]
        except subprocess.CalledProcessError:
            old = None
        try:
            new = json.loads(_git("show", f"{head}:{path}"))["invariants"]
        except subprocess.CalledProcessError:
            new = None
        if invariants_changed(old, new):
            changed.append(path)
    return changed


def invariants_changed(old, new):
    return old != new


def latest_approvals(reviews):
    latest = {}
    for review in reviews:
        user = review.get("user") or {}
        login = user.get("login")
        submitted_at = review.get("submitted_at")
        if not login or not submitted_at or user.get("type") == "Bot":
            continue
        submitted = datetime.fromisoformat(submitted_at.replace("Z", "+00:00"))
        if login not in latest or submitted > latest[login][0]:
            latest[login] = (submitted, review.get("state"))
    return latest


def has_fresh_architect_approval(reviews, author, allowed_reviewers, head_commit_time):
    latest = latest_approvals(reviews)
    for reviewer in allowed_reviewers:
        if reviewer == author or reviewer not in latest:
            continue
        submitted_at, state = latest[reviewer]
        if state == "APPROVED" and submitted_at >= head_commit_time:
            return True
    return False


def _fetch_reviews(api_url, repository, pull_number, token):
    reviews = []
    page = 1
    while True:
        url = (
            f"{api_url.rstrip('/')}/repos/{repository}/pulls/{pull_number}/reviews"
            f"?per_page=100&page={page}"
        )
        request = Request(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
            },
        )
        try:
            with urlopen(request, timeout=15) as response:
                page_reviews = json.loads(response.read())
        except HTTPError as error:
            raise RuntimeError(f"GitHub review API returned HTTP {error.code}.") from None
        except (URLError, TimeoutError) as error:
            reason = error.reason if isinstance(error, URLError) else "request timed out"
            raise RuntimeError(f"GitHub review API is unavailable: {reason}.") from None
        if not isinstance(page_reviews, list):
            raise ValueError("GitHub review API returned an invalid response.")
        reviews.extend(page_reviews)
        if len(page_reviews) < 100:
            return reviews
        page += 1


def main():
    if len(sys.argv) != 3:
        raise ValueError("Usage: require-invariant-approval.py BASE_SHA HEAD_SHA")
    base, head = sys.argv[1:]
    changed = changed_invariant_files(base, head)
    if not changed:
        print("No rationale invariants changed; human escalation is not required.")
        return

    reviewers = {
        login.strip().lstrip("@")
        for login in os.environ.get("EVENTHUB_ARCHITECT_REVIEWERS", "").split(",")
        if login.strip()
    }
    if not reviewers:
        raise ValueError("Protected architect reviewer allowlist is not configured.")
    event_path = os.environ.get("GITHUB_EVENT_PATH", "")
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    token = os.environ.get("GITHUB_TOKEN", "")
    api_url = os.environ.get("GITHUB_API_URL", "")
    if not all((event_path, repository, token, api_url)):
        raise ValueError("GitHub event, repository, API URL, and token are required.")
    event = json.loads(Path(event_path).read_text(encoding="utf-8"))
    pull_request = event.get("pull_request") or {}
    author = (pull_request.get("user") or {}).get("login")
    pull_number = pull_request.get("number")
    if not author or not pull_number:
        raise ValueError("GitHub event does not contain a pull request author and number.")

    head_commit_time = datetime.fromisoformat(
        _git("show", "-s", "--format=%cI", head).replace("Z", "+00:00")
    ).astimezone(timezone.utc)
    reviews = _fetch_reviews(api_url, repository, pull_number, token)
    if not has_fresh_architect_approval(reviews, author, reviewers, head_commit_time):
        raise ValueError(
            "Changed rationale invariants require a fresh approval from a configured "
            "architect other than the pull request author."
        )
    print(f"Architect approval verified for changed invariants in: {', '.join(changed)}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"Invariant review failed closed: {error}", file=sys.stderr)
        raise SystemExit(1) from None
