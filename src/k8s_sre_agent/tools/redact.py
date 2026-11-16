"""Best-effort secret redaction for anything that is about to reach a model or a log.

RBAC keeps ``secrets`` out of reach, but secrets still leak through logs, environment
variables in ``describe`` output and connection strings. This module is the last line of
defence, not the first: it errs on the side of redacting.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

REDACTED = "[REDACTED]"

_SENSITIVE_KEY = (
    r"[A-Za-z0-9_.-]*"
    r"(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|credential)"
    r"[A-Za-z0-9_.-]*"
)

# Order matters: whole-token patterns first, key/value pattern last.
_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)",
            re.DOTALL,
        ),
        REDACTED,
    ),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]*"), REDACTED),
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), REDACTED),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), REDACTED),
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"), rf"\1 {REDACTED}"),
    (re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://[^\s:/@]+):[^\s@/]+@"), rf"\1:{REDACTED}@"),
    (
        # key=value, key: value and "key": "value". Values that are Kubernetes references
        # ("<set to the key ...>") carry no secret and are kept as evidence.
        re.compile(
            rf"(?i)\b({_SENSITIVE_KEY})([\"']?\s*[:=]\s*)(?!<set to)"
            r"(\"[^\"\n]*\"|'[^'\n]*'|[^\s,;\"'}\]]+)"
        ),
        rf"\1\2{REDACTED}",
    ),
)


def redact(text: str) -> str:
    """Return ``text`` with likely secrets replaced by ``[REDACTED]``."""
    for pattern, replacement in _RULES:
        text = pattern.sub(replacement, text)
    return text


_SENSITIVE_KEY_RE = re.compile(rf"(?i)^{_SENSITIVE_KEY}$")


def redact_obj(value: Any) -> Any:
    """Recursively redact a JSON-like structure (used for audit-log arguments)."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, Mapping):
        return {
            k: REDACTED
            if isinstance(k, str) and _SENSITIVE_KEY_RE.match(k) and value[k] not in (None, "")
            else redact_obj(v)
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return [redact_obj(v) for v in value]
    return value
