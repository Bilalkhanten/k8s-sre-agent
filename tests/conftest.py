from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

from k8s_sre_agent.tools.audit import AuditLog
from k8s_sre_agent.tools.kubectl import Kubectl, RunResult


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "lab: needs a running kind lab (make lab-test)")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get("K8S_SRE_AGENT_LAB") == "1":
        return
    skip = pytest.mark.skip(reason="lab test: set K8S_SRE_AGENT_LAB=1 (see `make lab-test`)")
    for item in items:
        if "lab" in item.keywords:
            item.add_marker(skip)


class FakeKubectlRunner:
    """Stands in for the kubectl process: records argv, returns canned output per call."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self._responses: list[tuple[Callable[[Sequence[str]], bool], RunResult]] = []

    def respond(
        self, contains: str, stdout: str = "", stderr: str = "", returncode: int = 0
    ) -> None:
        self._responses.append(
            (lambda argv: contains in " ".join(argv), RunResult(returncode, stdout, stderr))
        )

    def __call__(self, argv: Sequence[str], timeout: float, env: dict[str, str]) -> RunResult:
        self.calls.append(list(argv))
        for matches, result in self._responses:
            if matches(argv):
                return result
        return RunResult(1, "", "no canned response")


@pytest.fixture
def kubeconfig(tmp_path: Path) -> Path:
    path = tmp_path / "agent.kubeconfig"
    path.write_text("apiVersion: v1\nkind: Config\n")
    return path


@pytest.fixture
def fake_runner() -> FakeKubectlRunner:
    return FakeKubectlRunner()


@pytest.fixture
def kubectl(kubeconfig: Path, fake_runner: FakeKubectlRunner) -> Kubectl:
    return Kubectl(kubeconfig, runner=fake_runner)


@pytest.fixture
def audit(tmp_path: Path) -> AuditLog:
    return AuditLog(tmp_path / "runs" / "audit.jsonl")
