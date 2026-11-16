from __future__ import annotations

from pathlib import Path

import pytest
from conftest import FakeKubectlRunner

from k8s_sre_agent.tools.base import ToolError
from k8s_sre_agent.tools.kubectl import Kubectl


def test_requires_an_existing_kubeconfig(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="agent-kubeconfig"):
        Kubectl(tmp_path / "missing")


@pytest.mark.parametrize("verb", ["delete", "apply", "exec", "patch", "edit", "create", "scale"])
def test_mutating_verbs_are_refused(
    kubectl: Kubectl, fake_runner: FakeKubectlRunner, verb: str
) -> None:
    with pytest.raises(ToolError, match="not permitted"):
        kubectl.run(verb, "pod", "x")
    assert fake_runner.calls == []


@pytest.mark.parametrize(
    "flag",
    [
        "--as=system:admin",
        "--token=abc",
        "--server=https://evil",
        "--kubeconfig=/root/.kube/config",
        "--context=prod",
        "--insecure-skip-tls-verify=true",
    ],
)
def test_identity_and_target_flags_are_refused(kubectl: Kubectl, flag: str) -> None:
    with pytest.raises(ToolError, match="not permitted"):
        kubectl.run("get", "pods", flag)


def test_always_pins_the_agent_kubeconfig(
    kubectl: Kubectl, fake_runner: FakeKubectlRunner, kubeconfig: Path
) -> None:
    fake_runner.respond("get pods", stdout="{}")
    kubectl.run("get", "pods")
    assert fake_runner.calls[0][:3] == ["kubectl", f"--kubeconfig={kubeconfig.resolve()}", "get"]


def test_nonzero_exit_becomes_a_tool_error(
    kubectl: Kubectl, fake_runner: FakeKubectlRunner
) -> None:
    fake_runner.respond(
        "get pods", stderr='pods is forbidden: User "system:serviceaccount"', returncode=1
    )
    with pytest.raises(ToolError, match="forbidden"):
        kubectl.run("get", "pods")
