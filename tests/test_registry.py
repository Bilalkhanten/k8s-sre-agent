from __future__ import annotations

import json

from pydantic import BaseModel, Field

from k8s_sre_agent.tools.audit import AuditLog
from k8s_sre_agent.tools.base import Tool, ToolError, ToolRegistry


class EchoArgs(BaseModel):
    text: str = Field(max_length=50)


def _registry(audit: AuditLog, handler, max_chars: int = 1000) -> ToolRegistry:  # type: ignore[no-untyped-def]
    return ToolRegistry([Tool("echo", "echo text", EchoArgs, handler)], audit, max_chars)


def _records(audit: AuditLog) -> list[dict[str, object]]:
    return [json.loads(line) for line in audit.path.read_text().splitlines()]


def test_success_is_returned_and_audited(audit: AuditLog) -> None:
    result = _registry(audit, lambda a: a.text.upper()).call("echo", {"text": "hi"})
    assert (result.ok, result.content) == (True, "HI")
    [record] = _records(audit)
    assert (
        record["tool"] == "echo" and record["ok"] is True and record["arguments"] == {"text": "hi"}
    )


def test_unknown_tool_is_an_error_not_an_exception(audit: AuditLog) -> None:
    result = _registry(audit, lambda a: a.text).call("nope", {})
    assert not result.ok and "unknown tool" in result.content and "echo" in result.content
    assert _records(audit)[0]["ok"] is False


def test_invalid_arguments_are_reported_to_the_model(audit: AuditLog) -> None:
    result = _registry(audit, lambda a: a.text).call("echo", {"text": "x" * 51})
    assert not result.ok and "invalid arguments" in result.content and "text" in result.content


def test_tool_error_message_is_passed_through(audit: AuditLog) -> None:
    def boom(_: EchoArgs) -> str:
        raise ToolError("pod not found")

    result = _registry(audit, boom).call("echo", {"text": "a"})
    assert (result.ok, result.content) == (False, "pod not found")


def test_unexpected_exception_does_not_leak_details_or_crash(audit: AuditLog) -> None:
    def boom(_: EchoArgs) -> str:
        raise RuntimeError("password=hunter2 in stack")

    result = _registry(audit, boom).call("echo", {"text": "a"})
    assert not result.ok and "hunter2" not in result.content
    assert "RuntimeError" in result.content


def test_output_is_redacted_and_truncated(audit: AuditLog) -> None:
    result = _registry(audit, lambda a: "DB_PASSWORD=hunter2\n" + "x" * 500, max_chars=100).call(
        "echo", {"text": "a"}
    )
    assert "hunter2" not in result.content
    assert result.content.endswith("[... output truncated ...]")
    assert _records(audit)[0]["truncated"] is True


def test_secrets_in_arguments_never_reach_the_audit_log(audit: AuditLog) -> None:
    _registry(audit, lambda a: "ok").call("echo", {"text": "token=abc123xyz"})
    assert "abc123xyz" not in audit.path.read_text()


def test_specs_are_llm_ready(audit: AuditLog) -> None:
    [spec] = _registry(audit, lambda a: a.text).specs()
    assert spec["name"] == "echo" and spec["input_schema"]["properties"]["text"]["maxLength"] == 50
