from __future__ import annotations

import pytest

from k8s_sre_agent.tools.redact import REDACTED, redact, redact_obj


@pytest.mark.parametrize(
    ("raw", "secret"),
    [
        ("DB_PASSWORD=hunter2", "hunter2"),
        ("    API_KEY:  sk-live-abcdef123456", "sk-live-abcdef123456"),
        ('{"password": "s3cr3t-value", "user": "bob"}', "s3cr3t-value"),
        ("client_secret = 'abc123def456'", "abc123def456"),
        ("Authorization: Bearer abcdefghijklmnop.qrstuv", "abcdefghijklmnop"),
        ("postgres://app:pa55w0rd@db.demo.svc:5432/app", "pa55w0rd"),
        ("aws key AKIAIOSFODNN7EXAMPLE in logs", "AKIAIOSFODNN7EXAMPLE"),
        ("token eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.sig-nature_1", "eyJhbGciOi"),
        ("ghp_abcdefghijklmnopqrstuvwxyz0123456789", "ghp_abcdefgh"),
        ("-----BEGIN RSA PRIVATE KEY-----\nMIIEow\n-----END RSA PRIVATE KEY-----", "MIIEow"),
    ],
)
def test_secrets_are_redacted(raw: str, secret: str) -> None:
    out = redact(raw)
    assert secret not in out
    assert REDACTED in out


@pytest.mark.parametrize(
    "text",
    [
        "Warning  OOMKilled  container worker exceeded its memory limit",
        "DB_PASSWORD:  <set to the key 'password' in secret 'db-creds'>",
        "Readiness probe failed: HTTP probe failed with statuscode: 404",
        'Error: secret "db-creds" not found',
        "Limits:\n  memory:  64Mi",
    ],
)
def test_ordinary_diagnostics_are_untouched(text: str) -> None:
    assert redact(text) == text


def test_redact_obj_handles_nested_structures() -> None:
    out = redact_obj({"pod": "a", "password": "x1", "nested": [{"token": "t0k3n"}, "ok"]})
    assert out == {"pod": "a", "password": REDACTED, "nested": [{"token": REDACTED}, "ok"]}
