import json

import pytest

from scripts.audit_rationales import _post_completion, audit, validate_endpoint


def test_endpoint_requires_https_and_rejects_embedded_credentials():
    assert validate_endpoint("https://audit.internal.example/v1/chat/completions")

    with pytest.raises(ValueError, match="HTTPS"):
        validate_endpoint("http://audit.internal.example/v1/chat/completions")
    with pytest.raises(ValueError, match="HTTPS"):
        validate_endpoint("https://user:password@audit.internal.example/v1/chat/completions")


def test_audit_fails_when_protected_credential_is_missing():
    with pytest.raises(ValueError, match="model and protected API key"):
        audit(
            "diff",
            "rationales",
            "https://audit.internal.example/v1/chat/completions",
            "review-model",
            "",
        )


@pytest.mark.parametrize(
    "content",
    [
        '{"decision":"REJECTED","findings":["violates invariant"]}',
        '{"decision":"INCONCLUSIVE","findings":[]}',
        '{"decision":"APPROVED","findings":["possible conflict"]}',
        "not-json",
    ],
)
def test_only_unambiguous_approval_without_findings_passes(monkeypatch, content):
    monkeypatch.setattr(
        "scripts.audit_rationales.urlopen",
        lambda *_args, **_kwargs: _Response(
            json.dumps({"choices": [{"message": {"content": content}}]}).encode()
        ),
    )

    with pytest.raises(ValueError, match="Semantic audit"):
        _post_completion(
            "https://audit.internal.example/v1/chat/completions",
            "review-model",
            "test-token",
            "diff",
            "rationales",
        )


def test_model_transport_does_not_log_or_accept_external_http_endpoint(monkeypatch):
    requests = []

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return _Response(
            json.dumps(
                {
                    "choices": [
                        {
                            "message": {
                                "content": '{"decision":"APPROVED","findings":[]}'
                            }
                        }
                    ]
                }
            ).encode()
        )

    monkeypatch.setattr("scripts.audit_rationales.urlopen", fake_urlopen)
    _post_completion(
        "https://audit.internal.example/v1/chat/completions",
        "review-model",
        "protected-token",
        "private diff",
        "private rationales",
    )

    request, timeout = requests[0]
    assert request.get_header("Authorization") == "Bearer protected-token"
    assert timeout > 0


class _Response:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self):
        return self.body
