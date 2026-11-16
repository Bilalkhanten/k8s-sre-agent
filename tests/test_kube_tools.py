"""Kube tools against canned kubectl output.

The fixtures are hand-written to match ``kubectl ... -o json``; replace them with captures from
the lab (``make lab-test`` exercises the real thing) when you have a running cluster.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from conftest import FakeKubectlRunner

from k8s_sre_agent.tools.audit import AuditLog
from k8s_sre_agent.tools.base import ToolRegistry
from k8s_sre_agent.tools.kube_tools import build_kube_tools
from k8s_sre_agent.tools.kubectl import Kubectl

NOW = datetime(2026, 11, 16, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def registry(kubectl: Kubectl, audit: AuditLog) -> ToolRegistry:
    return ToolRegistry(build_kube_tools(kubectl, clock=lambda: NOW), audit)


def _oom_pod() -> dict[str, Any]:
    return {
        "metadata": {
            "name": "memory-hog-7d9f-abcde",
            "labels": {"app": "memory-hog"},
            "creationTimestamp": "2026-11-16T11:50:00Z",
            "ownerReferences": [{"kind": "ReplicaSet", "name": "memory-hog-7d9f"}],
        },
        "spec": {"nodeName": "sre-agent-worker", "containers": [{"name": "worker"}]},
        "status": {
            "phase": "Running",
            "conditions": [{"type": "Ready", "status": "False", "reason": "ContainersNotReady"}],
            "containerStatuses": [
                {
                    "name": "worker",
                    "image": "polinux/stress",
                    "ready": False,
                    "restartCount": 5,
                    "state": {"waiting": {"reason": "CrashLoopBackOff", "message": "back-off"}},
                    "lastState": {
                        "terminated": {
                            "reason": "OOMKilled",
                            "exitCode": 137,
                            "finishedAt": "2026-11-16T11:59:00Z",
                        }
                    },
                }
            ],
        },
    }


def test_list_pods_surfaces_the_oom_evidence(
    registry: ToolRegistry, fake_runner: FakeKubectlRunner
) -> None:
    fake_runner.respond("get pods", stdout=json.dumps({"items": [_oom_pod()]}))
    result = registry.call("list_pods", {"namespace": "demo", "label_selector": "app=memory-hog"})
    assert result.ok
    [pod] = json.loads(result.content)
    assert pod["ready"] == "0/1" and pod["restarts"] == 5 and pod["age_seconds"] == 600
    assert pod["owner"] == "ReplicaSet/memory-hog-7d9f"
    assert pod["containers"][0]["last_terminated"] == {
        "reason": "OOMKilled",
        "exit_code": 137,
        "finished_at": "2026-11-16T11:59:00Z",
    }
    assert pod["unmet_conditions"][0]["reason"] == "ContainersNotReady"
    argv = fake_runner.calls[0]
    assert "--namespace=demo" in argv and "--selector=app=memory-hog" in argv


def test_get_events_filters_sorts_and_limits(
    registry: ToolRegistry, fake_runner: FakeKubectlRunner
) -> None:
    def event(ts: str, obj: str, kind: str, etype: str, reason: str) -> dict[str, Any]:
        return {
            "lastTimestamp": ts,
            "type": etype,
            "reason": reason,
            "message": f"{reason} for {obj}",
            "count": 2,
            "involvedObject": {"kind": kind, "name": obj},
        }

    items = [
        event("2026-11-16T11:58:00Z", "web-1", "Pod", "Warning", "BackOff"),
        event("2026-11-16T11:50:00Z", "web-1", "Pod", "Normal", "Pulled"),
        event("2026-11-16T11:55:00Z", "other", "Pod", "Warning", "Failed"),
    ]
    fake_runner.respond("get events", stdout=json.dumps({"items": items}))

    out = json.loads(
        registry.call(
            "get_events", {"namespace": "demo", "involved_object": "web-1", "warnings_only": False}
        ).content
    )
    assert [e["reason"] for e in out] == ["Pulled", "BackOff"]  # oldest first, filtered

    out = json.loads(
        registry.call(
            "get_events", {"namespace": "demo", "warnings_only": True, "limit": 1}
        ).content
    )
    assert [e["reason"] for e in out] == ["BackOff"]  # most recent warning only


def test_get_logs_builds_a_bounded_command(
    registry: ToolRegistry, fake_runner: FakeKubectlRunner
) -> None:
    fake_runner.respond("logs", stdout="2026-11-16T11:59:00Z ERROR upstream timed out\n")
    result = registry.call(
        "get_logs",
        {"namespace": "demo", "pod": "storefront-abc", "container": "client", "previous": True},
    )
    assert result.ok and "timed out" in result.content
    argv = fake_runner.calls[0]
    assert {"--tail=100", "--previous", "--container=client", "--timestamps"} <= set(argv)
    assert any(a.startswith("--limit-bytes=") for a in argv)


def test_get_logs_redacts_secrets(registry: ToolRegistry, fake_runner: FakeKubectlRunner) -> None:
    fake_runner.respond("logs", stdout="connecting with password=hunter2 to db\n")
    result = registry.call("get_logs", {"namespace": "demo", "pod": "api-1"})
    assert "hunter2" not in result.content


def test_describe_runs_kubectl_describe(
    registry: ToolRegistry, fake_runner: FakeKubectlRunner
) -> None:
    fake_runner.respond("describe networkpolicy", stdout="Name: restrict-inventory-ingress\n")
    result = registry.call(
        "describe",
        {"kind": "networkpolicy", "name": "restrict-inventory-ingress", "namespace": "demo"},
    )
    assert result.ok and "restrict-inventory-ingress" in result.content
    assert fake_runner.calls[0][-3:] == [
        "networkpolicy",
        "restrict-inventory-ingress",
        "--namespace=demo",
    ]


def test_describe_needs_a_namespace_for_namespaced_kinds(registry: ToolRegistry) -> None:
    result = registry.call("describe", {"kind": "pod", "name": "x"})
    assert not result.ok and "namespace is required" in result.content


def test_describe_node_is_cluster_scoped(
    registry: ToolRegistry, fake_runner: FakeKubectlRunner
) -> None:
    fake_runner.respond("describe node", stdout="Name: sre-agent-worker\n")
    assert registry.call("describe", {"kind": "node", "name": "sre-agent-worker"}).ok
    assert not any(a.startswith("--namespace") for a in fake_runner.calls[0])


def test_describe_configmap_never_returns_values(
    registry: ToolRegistry, fake_runner: FakeKubectlRunner
) -> None:
    cm = {
        "data": {"LOG_LEVEL": "debug", "DB_URL": "postgres://u:p@h/db"},
        "binaryData": {"cert": "AA=="},
    }
    fake_runner.respond("get configmap", stdout=json.dumps(cm))
    result = registry.call(
        "describe", {"kind": "configmap", "name": "app-config", "namespace": "demo"}
    )
    body = json.loads(result.content)
    assert set(body["keys"]) == {"LOG_LEVEL", "DB_URL"} and body["binary_keys"] == ["cert"]
    assert "debug" not in result.content and "postgres" not in result.content
    assert [call[2] for call in fake_runner.calls] == ["get"]  # never `describe configmap`


@pytest.mark.parametrize(
    "arguments",
    [
        {"namespace": "demo; rm -rf /"},
        {"namespace": "--all-namespaces"},
        {"namespace": "demo", "label_selector": "--overrides={}"},
        {"namespace": "Demo"},
    ],
)
def test_argument_injection_is_rejected_before_kubectl_runs(
    registry: ToolRegistry, fake_runner: FakeKubectlRunner, arguments: dict[str, str]
) -> None:
    result = registry.call("list_pods", arguments)
    assert not result.ok and "invalid arguments" in result.content
    assert fake_runner.calls == []


def test_secrets_cannot_be_described(
    registry: ToolRegistry, fake_runner: FakeKubectlRunner
) -> None:
    result = registry.call("describe", {"kind": "secret", "name": "db", "namespace": "demo"})
    assert not result.ok and fake_runner.calls == []


def test_rbac_denial_is_surfaced(registry: ToolRegistry, fake_runner: FakeKubectlRunner) -> None:
    fake_runner.respond("get pods", stderr="pods is forbidden", returncode=1)
    result = registry.call("list_pods", {"namespace": "kube-system"})
    assert not result.ok and "forbidden" in result.content
