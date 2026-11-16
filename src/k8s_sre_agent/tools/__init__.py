"""Read-only investigation tools. See ``build_registry`` for the standard wiring."""

from __future__ import annotations

from pathlib import Path

from k8s_sre_agent.tools.audit import AuditLog
from k8s_sre_agent.tools.base import Tool, ToolError, ToolRegistry, ToolResult
from k8s_sre_agent.tools.kube_tools import build_kube_tools
from k8s_sre_agent.tools.kubectl import Kubectl
from k8s_sre_agent.tools.prometheus import Prometheus, build_prometheus_tool


def build_registry(*, kubeconfig: Path, prometheus_url: str, audit_path: Path) -> ToolRegistry:
    """The standard tool set: kube tools bound to the read-only kubeconfig, plus PromQL."""
    tools = [
        *build_kube_tools(Kubectl(kubeconfig)),
        build_prometheus_tool(Prometheus(prometheus_url)),
    ]
    return ToolRegistry(tools, audit=AuditLog(audit_path))


__all__ = ["Tool", "ToolError", "ToolRegistry", "ToolResult", "build_registry"]
