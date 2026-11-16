"""Read-only PromQL access.

Kubernetes RBAC does not govern Prometheus, so read-only-ness is structural here: the client
can only issue GETs to ``/api/v1/query`` and ``/api/v1/query_range``. The base URL is set by
the operator, never by the model. Keep Prometheus' admin API disabled (the chart default).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, Field

from k8s_sre_agent.tools.base import Tool, ToolError

MAX_SERIES = 10
MAX_POINTS = 20


class Prometheus:
    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 10.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError(f"invalid Prometheus URL: {base_url!r}")
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            transport=transport,
            follow_redirects=False,
            trust_env=False,
        )

    def _get(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        try:
            response = self._client.get(path, params=params)
        except httpx.HTTPError as exc:
            raise ToolError(f"Prometheus unreachable: {type(exc).__name__}") from exc
        try:
            body = response.json()
        except ValueError as exc:
            raise ToolError(f"Prometheus returned non-JSON (HTTP {response.status_code})") from exc
        if body.get("status") != "success":
            detail = body.get("error") or f"HTTP {response.status_code}"
            raise ToolError(f"Prometheus rejected the query: {detail}")
        data = body.get("data")
        if not isinstance(data, dict):
            raise ToolError("unexpected Prometheus response")
        return data

    def instant(self, query: str) -> dict[str, Any]:
        return self._get("/api/v1/query", {"query": query})

    def range(
        self, query: str, *, minutes: int, step_seconds: int, now: datetime
    ) -> dict[str, Any]:
        start = now - timedelta(minutes=minutes)
        return self._get(
            "/api/v1/query_range",
            {
                "query": query,
                "start": f"{start.timestamp():.3f}",
                "end": f"{now.timestamp():.3f}",
                "step": f"{step_seconds}s",
            },
        )


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _downsample(values: list[Any]) -> list[Any]:
    if len(values) <= MAX_POINTS:
        return values
    stride = -(-len(values) // MAX_POINTS)
    sampled = values[::stride]
    return sampled if sampled[-1] is values[-1] else [*sampled, values[-1]]


def summarize_result(data: dict[str, Any]) -> dict[str, Any]:
    result_type = data.get("resultType")
    result = data.get("result", [])
    series: list[dict[str, Any]] = []
    for item in result[:MAX_SERIES]:
        entry: dict[str, Any] = {"metric": item.get("metric", {})}
        if result_type == "matrix":
            entry["values"] = [[_iso(t), v] for t, v in _downsample(item.get("values", []))]
        elif "value" in item:
            t, v = item["value"]
            entry["value"] = [_iso(t), v]
        series.append(entry)
    summary: dict[str, Any] = {"result_type": result_type, "series": series}
    if len(result) > MAX_SERIES:
        summary["omitted_series"] = len(result) - MAX_SERIES
    return summary


class PromQLArgs(BaseModel):
    query: str = Field(min_length=1, max_length=2000, description="PromQL expression.")
    range_minutes: int | None = Field(
        default=None,
        ge=1,
        le=360,
        description="If set, query the last N minutes as a time series instead of one instant.",
    )
    step_seconds: int = Field(default=60, ge=15, le=3600, description="Step for range queries.")


def build_prometheus_tool(
    prometheus: Prometheus, clock: Callable[[], datetime] | None = None
) -> Tool[Any]:
    now_fn = clock or (lambda: datetime.now(UTC))

    def handler(args: PromQLArgs) -> str:
        if args.range_minutes is None:
            data = prometheus.instant(args.query)
        else:
            data = prometheus.range(
                args.query,
                minutes=args.range_minutes,
                step_seconds=args.step_seconds,
                now=now_fn(),
            )
        return json.dumps(summarize_result(data), ensure_ascii=False, separators=(",", ":"))

    return Tool(
        "query_prometheus",
        "Run a read-only PromQL query (instant, or a time series over the last N minutes). "
        "Useful for restarts, memory/CPU versus limits, and alert state.",
        PromQLArgs,
        handler,
    )
