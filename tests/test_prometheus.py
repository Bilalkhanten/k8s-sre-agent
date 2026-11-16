from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest

from k8s_sre_agent.tools.audit import AuditLog
from k8s_sre_agent.tools.base import ToolRegistry
from k8s_sre_agent.tools.prometheus import MAX_POINTS, MAX_SERIES, Prometheus, build_prometheus_tool

NOW = datetime(2026, 11, 16, 12, 0, 0, tzinfo=UTC)


def _registry(handler: httpx.MockTransport, audit: AuditLog) -> ToolRegistry:
    prom = Prometheus("http://prom.test:9090", transport=handler)
    return ToolRegistry([build_prometheus_tool(prom, clock=lambda: NOW)], audit)


def test_instant_query(audit: AuditLog) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "resultType": "vector",
                    "result": [{"metric": {"pod": "a"}, "value": [1_700_000_000, "3"]}],
                },
            },
        )

    result = _registry(httpx.MockTransport(handler), audit).call(
        "query_prometheus", {"query": "kube_pod_container_status_restarts_total"}
    )
    body = json.loads(result.content)
    assert body["series"][0]["metric"] == {"pod": "a"} and body["series"][0]["value"][1] == "3"
    assert seen[0].method == "GET" and seen[0].url.path == "/api/v1/query"


def test_range_query_is_downsampled_and_capped(audit: AuditLog) -> None:
    values = [[1_700_000_000 + i * 60, str(i)] for i in range(120)]
    series = [{"metric": {"i": str(n)}, "values": values} for n in range(MAX_SERIES + 5)]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/query_range"
        assert request.url.params["step"] == "60s"
        return httpx.Response(
            200, json={"status": "success", "data": {"resultType": "matrix", "result": series}}
        )

    result = _registry(httpx.MockTransport(handler), audit).call(
        "query_prometheus", {"query": "up", "range_minutes": 60}
    )
    body = json.loads(result.content)
    assert len(body["series"]) == MAX_SERIES and body["omitted_series"] == 5
    assert len(result.content) < 20_000  # well inside the registry's output cap
    assert len(body["series"][0]["values"]) <= MAX_POINTS + 1
    assert body["series"][0]["values"][-1][1] == "119"  # last point always kept


def test_bad_query_error_reaches_the_model(audit: AuditLog) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"status": "error", "errorType": "bad_data", "error": "parse error: unexpected )"},
        )

    result = _registry(httpx.MockTransport(handler), audit).call(
        "query_prometheus", {"query": "up)"}
    )
    assert not result.ok and "parse error" in result.content


def test_unreachable_prometheus_is_a_clean_error(audit: AuditLog) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    result = _registry(httpx.MockTransport(handler), audit).call(
        "query_prometheus", {"query": "up"}
    )
    assert not result.ok and "unreachable" in result.content


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://x", "prom:9090", ""])
def test_only_http_urls_are_accepted(url: str) -> None:
    with pytest.raises(ValueError):
        Prometheus(url)


def test_range_bounds_are_enforced(audit: AuditLog) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not be called")

    result = _registry(httpx.MockTransport(handler), audit).call(
        "query_prometheus", {"query": "up", "range_minutes": 100_000}
    )
    assert not result.ok and "invalid arguments" in result.content
