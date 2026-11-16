"""Tool abstraction: typed arguments in, bounded and redacted text out, always audited."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ValidationError

from k8s_sre_agent.tools.audit import AuditLog, AuditRecord, utc_now_iso
from k8s_sre_agent.tools.redact import redact, redact_obj

ArgsT = TypeVar("ArgsT", bound=BaseModel)

DEFAULT_MAX_OUTPUT_CHARS = 20_000
TRUNCATION_MARKER = "\n[... output truncated ...]"


class ToolError(Exception):
    """An expected failure whose message is safe and useful to show to the model."""


@dataclass(frozen=True)
class Tool(Generic[ArgsT]):
    name: str
    description: str
    args_model: type[ArgsT]
    handler: Callable[[ArgsT], str]

    def spec(self) -> dict[str, Any]:
        """Tool definition in the shape LLM tool-use APIs expect."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.args_model.model_json_schema(),
        }


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    content: str


class ToolRegistry:
    """Dispatches tool calls. Every call is validated, redacted, bounded and audit-logged."""

    def __init__(
        self,
        tools: Iterable[Tool[Any]],
        audit: AuditLog,
        max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS,
    ) -> None:
        self._tools = {t.name: t for t in tools}
        self._audit = audit
        self._max_output_chars = max_output_chars

    def specs(self) -> list[dict[str, Any]]:
        return [t.spec() for t in self._tools.values()]

    def call(self, name: str, arguments: Mapping[str, Any]) -> ToolResult:
        started = time.monotonic()
        truncated = False
        error: str | None = None
        content = ""
        try:
            tool = self._tools.get(name)
            if tool is None:
                raise ToolError(f"unknown tool {name!r}; available: {sorted(self._tools)}")
            try:
                args = tool.args_model.model_validate(dict(arguments))
            except ValidationError as exc:
                raise ToolError(f"invalid arguments: {_summarise(exc)}") from exc
            content = tool.handler(args)
        except ToolError as exc:
            error = str(exc)
        except Exception as exc:  # boundary: a tool bug must not crash the agent loop
            error = f"internal error in tool {name!r}: {type(exc).__name__}"

        ok = error is None
        text = redact(content if ok else (error or ""))
        if len(text) > self._max_output_chars:
            text = text[: self._max_output_chars] + TRUNCATION_MARKER
            truncated = True

        self._audit.write(
            AuditRecord(
                ts=utc_now_iso(),
                tool=name,
                arguments=redact_obj(dict(arguments)),
                ok=ok,
                duration_ms=int((time.monotonic() - started) * 1000),
                output_chars=len(text),
                truncated=truncated,
                error=redact(error) if error else None,
            )
        )
        return ToolResult(ok=ok, content=text)


def _summarise(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors(include_url=False, include_input=False):
        loc = ".".join(str(p) for p in err["loc"]) or "arguments"
        parts.append(f"{loc}: {err['msg']}")
    return "; ".join(parts)
