"""Append-only JSONL audit log: one record per tool call, whatever the outcome."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AuditRecord:
    ts: str
    tool: str
    arguments: dict[str, Any]
    ok: bool
    duration_ms: int
    output_chars: int
    truncated: bool
    error: str | None = None


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


class AuditLog:
    """Writes :class:`AuditRecord` lines to ``path`` (created with owner-only permissions)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, record: AuditRecord) -> None:
        line = json.dumps(asdict(record), sort_keys=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        self.path.chmod(0o600)
