"""Integration tests: the real tools against a real lab cluster.

These are the checks that cannot run in CI. Run them on your machine:

    make lab-up
    make agent-kubeconfig
    make lab-test

They inject scenarios with the admin context and read them back with the read-only agent
kubeconfig, so they double as a check that the RBAC is sufficient for the evidence each
scenario's ``expected.json`` asks for.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest

from k8s_sre_agent.tools import ToolRegistry, build_registry

pytestmark = pytest.mark.lab

REPO = Path(__file__).resolve().parent.parent
KUBECONFIG = Path(os.environ.get("AGENT_KUBECONFIG", REPO / ".kube" / "agent.kubeconfig"))
PROMETHEUS_URL = os.environ.get("PROMETHEUS_URL", "http://localhost:9090")


def _kubectl(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["kubectl", f"--kubeconfig={KUBECONFIG}", *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _wait_for(check: Callable[[], bool], what: str, timeout: float = 180.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(3)
    pytest.fail(f"timed out after {timeout:.0f}s waiting for {what}")


@contextmanager
def scenario(scenario_id: str) -> Iterator[None]:
    script = str(REPO / "scripts" / "scenario.sh")
    subprocess.run([script, "up", scenario_id], cwd=REPO, check=True)
    try:
        yield
    finally:
        subprocess.run([script, "down", scenario_id], cwd=REPO, check=False)


@pytest.fixture(scope="module")
def registry(tmp_path_factory: pytest.TempPathFactory) -> ToolRegistry:
    if not KUBECONFIG.is_file():
        pytest.fail(f"{KUBECONFIG} not found - run `make agent-kubeconfig` first")
    return build_registry(
        kubeconfig=KUBECONFIG,
        prometheus_url=PROMETHEUS_URL,
        audit_path=tmp_path_factory.mktemp("audit") / "audit.jsonl",
    )


def _json(registry: ToolRegistry, tool: str, **arguments: Any) -> Any:
    result = registry.call(tool, arguments)
    assert result.ok, result.content
    return json.loads(result.content)


# ----------------------------------------------------------------------------- RBAC


@pytest.mark.parametrize(
    ("verb_and_resource", "allowed"),
    [
        (["get", "pods", "--all-namespaces"], "yes"),
        (["list", "events", "--all-namespaces"], "yes"),
        (["get", "pods/log", "-n", "demo"], "yes"),
        (["get", "secrets", "--all-namespaces"], "no"),
        (["delete", "pods", "-n", "demo"], "no"),
        (["create", "pods", "-n", "demo"], "no"),
        (["patch", "deployments", "-n", "demo"], "no"),
        (["create", "pods/exec", "-n", "demo"], "no"),
    ],
)
def test_service_account_permissions(verb_and_resource: list[str], allowed: str) -> None:
    out = _kubectl("auth", "can-i", *verb_and_resource)
    assert out.stdout.strip() == allowed, out.stderr


# ----------------------------------------------------------------------- baseline


def test_healthy_workload_is_visible(registry: ToolRegistry) -> None:
    pods = _json(registry, "list_pods", namespace="demo", label_selector="app=podinfo")
    assert pods and all(p["phase"] == "Running" and p["ready"] == "1/1" for p in pods)
    assert (
        "podinfo"
        in registry.call(
            "describe", {"kind": "deployment", "name": "podinfo", "namespace": "demo"}
        ).content
    )


# --------------------------------------------------------------------- scenarios


def test_oom_killed_evidence(registry: ToolRegistry) -> None:
    with scenario("oom-killed"):

        def oom_seen() -> bool:
            pods = _json(registry, "list_pods", namespace="demo", label_selector="app=memory-hog")
            return any(
                (c.get("last_terminated") or {}).get("reason") == "OOMKilled"
                for p in pods
                for c in p["containers"]
            )

        _wait_for(oom_seen, "an OOMKilled termination")
        [pod, *_] = _json(registry, "list_pods", namespace="demo", label_selector="app=memory-hog")
        last = next(c["last_terminated"] for c in pod["containers"] if c.get("last_terminated"))
        assert (last["reason"], last["exit_code"]) == ("OOMKilled", 137)

        described = registry.call(
            "describe", {"kind": "pod", "name": pod["name"], "namespace": "demo"}
        ).content
        assert "64Mi" in described and "200M" in described


def test_networkpolicy_block_evidence(registry: ToolRegistry) -> None:
    with scenario("networkpolicy-block"):
        policy = registry.call(
            "describe",
            {"kind": "networkpolicy", "name": "restrict-inventory-ingress", "namespace": "demo"},
        ).content
        assert "app=inventory" in policy and "app=billing" in policy

        pods = _json(registry, "list_pods", namespace="demo", label_selector="app=storefront")
        assert pods and pods[0]["labels"]["app"] == "storefront"
        storefront = pods[0]["name"]

        def timeouts_logged() -> bool:
            logs = registry.call("get_logs", {"namespace": "demo", "pod": storefront}).content
            return "timed out" in logs

        # If this times out, your kind CNI does not enforce NetworkPolicy: see lab/README.md.
        _wait_for(timeouts_logged, "storefront timeouts (does the CNI enforce NetworkPolicy?)")


def test_image_pull_backoff_evidence(registry: ToolRegistry) -> None:
    with scenario("image-pull-backoff"):

        def backoff() -> bool:
            events = _json(registry, "get_events", namespace="demo", warnings_only=True)
            return any(e["reason"] in {"Failed", "BackOff"} for e in events)

        _wait_for(backoff, "image pull warning events")
        pods = _json(registry, "list_pods", namespace="demo", label_selector="app=bad-image")
        reasons = {c.get("reason") for p in pods for c in p["containers"]}
        assert reasons & {"ErrImagePull", "ImagePullBackOff"}


# ------------------------------------------------------------------- prometheus


def test_prometheus_instant_query(registry: ToolRegistry) -> None:
    try:
        httpx.get(f"{PROMETHEUS_URL}/-/ready", timeout=3, trust_env=False)
    except httpx.HTTPError:
        pytest.skip(
            "Prometheus not reachable; run: kubectl -n monitoring port-forward "
            "svc/monitoring-kube-prometheus-prometheus 9090:9090"
        )
    body = _json(registry, "query_prometheus", query="up")
    assert body["series"], "expected at least one 'up' series"
