#!/usr/bin/env python3
"""Fail-closed semantic review against the versioned architecture rationales."""

import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
MAX_CONTEXT_CHARS = 160_000
TIMEOUT_SECONDS = 60


def validate_endpoint(endpoint):
    parsed = urlsplit(endpoint)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise ValueError("Semantic audit endpoint must be an HTTPS URL without embedded credentials.")
    return endpoint


def changed_patch(base, head):
    result = subprocess.run(
        ["git", "diff", "--no-ext-diff", "--unified=60", f"{base}...{head}", "--", "."],
        check=True,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    if not result.stdout.strip():
        raise ValueError("Pull request diff is empty; refusing to report an audit pass.")
    return result.stdout


def load_rationales():
    paths = sorted((ROOT / "docs/rationales").glob("ADR-*.json"))
    if not paths:
        raise ValueError("No versioned architecture rationales were found.")
    rationales = []
    for path in paths:
        content = path.read_text(encoding="utf-8")
        json.loads(content)
        rationales.append(f"### {path.relative_to(ROOT)}\n{content}")
    return "\n\n".join(rationales)


def _post_completion(endpoint, model, api_key, diff, rationales):
    payload = {
        "model": model,
        "temperature": 0,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Review the proposed pull request against the supplied, versioned "
                    "architecture rationales. Return only JSON with exactly the shape "
                    '{"decision":"APPROVED|REJECTED|INCONCLUSIVE","findings":[]} . '
                    "Approve only when every material change is consistent with the "
                    "rationales. Uncertainty, missing context, or a required rationale "
                    "update is INCONCLUSIVE. Findings must identify concrete conflicts."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Existing architecture rationales:\n"
                    f"{rationales}\n\nPull request diff:\n{diff}"
                ),
            },
        ],
        "response_format": {"type": "json_object"},
    }
    request = Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            response_body = response.read()
    except HTTPError as error:
        raise RuntimeError(f"Semantic audit endpoint returned HTTP {error.code}.") from None
    except (URLError, TimeoutError):
        raise RuntimeError(
            "Semantic audit endpoint is unavailable (connection error or timeout)."
        ) from None

    try:
        envelope = json.loads(response_body)
        content = envelope["choices"][0]["message"]["content"]
        result = json.loads(content)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
        raise ValueError("Semantic audit endpoint returned an invalid response.") from None

    if (
        not isinstance(result, dict)
        or set(result) != {"decision", "findings"}
        or result.get("decision") != "APPROVED"
        or not isinstance(result.get("findings"), list)
        or result.get("findings") != []
    ):
        raise ValueError("Semantic audit did not return an unambiguous approval with no findings.")


def audit(diff, rationales, endpoint, model, api_key):
    validate_endpoint(endpoint)
    if not model or not api_key:
        raise ValueError("Semantic audit model and protected API key are required.")
    context = f"{rationales}\n\n{diff}"
    if len(context) > MAX_CONTEXT_CHARS:
        raise ValueError("Semantic audit input exceeds the configured context limit.")
    _post_completion(endpoint, model, api_key, diff, rationales)


def main():
    if len(sys.argv) != 3:
        raise ValueError("Usage: audit-rationales.py BASE_SHA HEAD_SHA")
    endpoint = os.environ.get("EVENTHUB_SEMANTIC_AUDIT_ENDPOINT", "")
    model = os.environ.get("EVENTHUB_SEMANTIC_AUDIT_MODEL", "")
    api_key = os.environ.get("EVENTHUB_SEMANTIC_AUDIT_API_KEY", "")
    if not endpoint or not model or not api_key:
        raise ValueError(
            "Semantic audit endpoint, model, and protected API key must be configured."
        )
    diff = changed_patch(sys.argv[1], sys.argv[2])
    rationales = load_rationales()
    audit(diff, rationales, endpoint, model, api_key)
    print("Semantic audit approved the pull request with no findings.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"Semantic audit failed closed: {error}", file=sys.stderr)
        raise SystemExit(1) from None
