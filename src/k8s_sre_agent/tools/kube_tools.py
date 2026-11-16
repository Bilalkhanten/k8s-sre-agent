"""Read-only Kubernetes tools: pods, events, logs and describe.

Output is distilled to the fields an investigator needs. That keeps token use down and means
whole objects (with env vars, annotations, managed fields) never reach the model.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, StringConstraints

from k8s_sre_agent.tools.base import Tool, ToolError
from k8s_sre_agent.tools.kubectl import Kubectl

Name = Annotated[
    str,
    StringConstraints(max_length=253, pattern=r"^[a-z0-9]([-a-z0-9.]*[a-z0-9])?$"),
]
Selector = Annotated[
    str,
    StringConstraints(max_length=253, pattern=r"^[A-Za-z0-9!][A-Za-z0-9_./=!,() -]*$"),
]

Clock = Callable[[], datetime]
MESSAGE_CHARS = 300

# describe-able kinds -> kubectl resource name. ``configmap`` is handled separately (keys only).
DESCRIBE_KINDS: dict[str, str] = {
    "pod": "pod",
    "deployment": "deployment",
    "replicaset": "replicaset",
    "statefulset": "statefulset",
    "daemonset": "daemonset",
    "job": "job",
    "cronjob": "cronjob",
    "service": "service",
    "endpoints": "endpoints",
    "ingress": "ingress",
    "networkpolicy": "networkpolicy",
    "resourcequota": "resourcequota",
    "limitrange": "limitrange",
    "persistentvolumeclaim": "persistentvolumeclaim",
    "configmap": "configmap",
    "node": "node",
    "namespace": "namespace",
}
CLUSTER_SCOPED = {"node", "namespace"}


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _parse_json(raw: str, what: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ToolError(f"could not parse kubectl output for {what}") from exc
    if not isinstance(parsed, dict):
        raise ToolError(f"unexpected kubectl output for {what}")
    return parsed


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _age_seconds(value: str | None, now: datetime) -> int | None:
    ts = _parse_time(value)
    return max(int((now - ts).total_seconds()), 0) if ts else None


def _clip(message: str | None) -> str | None:
    if message is None:
        return None
    return message if len(message) <= MESSAGE_CHARS else message[:MESSAGE_CHARS] + "..."


# --------------------------------------------------------------------------- pods


def _container_view(status: dict[str, Any]) -> dict[str, Any]:
    state_name, state = next(iter((status.get("state") or {"unknown": {}}).items()))
    view: dict[str, Any] = {
        "name": status.get("name"),
        "image": status.get("image"),
        "ready": status.get("ready", False),
        "restarts": status.get("restartCount", 0),
        "state": state_name,
    }
    if state.get("reason"):
        view["reason"] = state["reason"]
    if state.get("exitCode") is not None:
        view["exit_code"] = state["exitCode"]
    if state.get("message"):
        view["message"] = _clip(state["message"])
    last = (status.get("lastState") or {}).get("terminated")
    if last:
        view["last_terminated"] = {
            "reason": last.get("reason"),
            "exit_code": last.get("exitCode"),
            "finished_at": last.get("finishedAt"),
        }
    return view


def summarize_pod(pod: dict[str, Any], now: datetime) -> dict[str, Any]:
    meta = pod.get("metadata", {})
    spec = pod.get("spec", {})
    status = pod.get("status", {})
    statuses = status.get("containerStatuses", [])
    owners = meta.get("ownerReferences") or []
    summary: dict[str, Any] = {
        "name": meta.get("name"),
        "labels": meta.get("labels", {}),
        "phase": status.get("phase"),
        "ready": f"{sum(1 for s in statuses if s.get('ready'))}/{len(spec.get('containers', []))}",
        "restarts": sum(s.get("restartCount", 0) for s in statuses),
        "node": spec.get("nodeName"),
        "age_seconds": _age_seconds(meta.get("creationTimestamp"), now),
        "containers": [_container_view(s) for s in statuses],
    }
    if owners:
        summary["owner"] = f"{owners[0].get('kind')}/{owners[0].get('name')}"
    if status.get("reason"):
        summary["reason"] = status["reason"]
    if meta.get("deletionTimestamp"):
        summary["terminating"] = True
    if status.get("initContainerStatuses"):
        summary["init_containers"] = [_container_view(s) for s in status["initContainerStatuses"]]
    failing = [
        {"type": c.get("type"), "reason": c.get("reason"), "message": _clip(c.get("message"))}
        for c in status.get("conditions", [])
        if c.get("status") != "True"
    ]
    if failing:
        summary["unmet_conditions"] = failing
    return summary


class ListPodsArgs(BaseModel):
    namespace: Name = Field(description="Namespace to list pods in.")
    label_selector: Selector | None = Field(
        default=None, description="Optional label selector, e.g. 'app=storefront'."
    )


def _list_pods(kubectl: Kubectl, clock: Clock) -> Callable[[ListPodsArgs], str]:
    def handler(args: ListPodsArgs) -> str:
        cmd = ["pods", f"--namespace={args.namespace}", "--output=json"]
        if args.label_selector:
            cmd.append(f"--selector={args.label_selector}")
        data = _parse_json(kubectl.run("get", *cmd), "pods")
        now = clock()
        return _dump([summarize_pod(p, now) for p in data.get("items", [])])

    return handler


# ------------------------------------------------------------------------- events


def _event_time(event: dict[str, Any]) -> str:
    return (
        event.get("lastTimestamp")
        or event.get("eventTime")
        or event.get("metadata", {}).get("creationTimestamp")
        or ""
    )


def summarize_event(event: dict[str, Any]) -> dict[str, Any]:
    obj = event.get("involvedObject", {})
    return {
        "time": _event_time(event),
        "type": event.get("type"),
        "reason": event.get("reason"),
        "object": f"{obj.get('kind')}/{obj.get('name')}",
        "count": event.get("count", 1),
        "message": _clip(event.get("message")),
    }


class GetEventsArgs(BaseModel):
    namespace: Name = Field(description="Namespace to read events from.")
    involved_object: Name | None = Field(
        default=None, description="Only events about this object name (pod, deployment, ...)."
    )
    warnings_only: bool = Field(default=False, description="Only events of type Warning.")
    limit: int = Field(default=40, ge=1, le=200, description="Most recent N events to return.")


def _get_events(kubectl: Kubectl) -> Callable[[GetEventsArgs], str]:
    def handler(args: GetEventsArgs) -> str:
        data = _parse_json(
            kubectl.run("get", "events", f"--namespace={args.namespace}", "--output=json"),
            "events",
        )
        events = data.get("items", [])
        if args.involved_object:
            events = [
                e for e in events if e.get("involvedObject", {}).get("name") == args.involved_object
            ]
        if args.warnings_only:
            events = [e for e in events if e.get("type") == "Warning"]
        events.sort(key=_event_time)
        return _dump([summarize_event(e) for e in events[-args.limit :]])

    return handler


# --------------------------------------------------------------------------- logs


class GetLogsArgs(BaseModel):
    namespace: Name = Field(description="Namespace of the pod.")
    pod: Name = Field(description="Pod name.")
    container: Name | None = Field(
        default=None, description="Container name; required for multi-container pods."
    )
    previous: bool = Field(
        default=False, description="Logs of the previous (crashed) container instance."
    )
    tail_lines: int = Field(default=100, ge=1, le=500, description="Number of trailing lines.")


def _get_logs(kubectl: Kubectl) -> Callable[[GetLogsArgs], str]:
    def handler(args: GetLogsArgs) -> str:
        cmd = [
            args.pod,
            f"--namespace={args.namespace}",
            f"--tail={args.tail_lines}",
            "--limit-bytes=262144",
            "--timestamps",
        ]
        if args.container:
            cmd.append(f"--container={args.container}")
        if args.previous:
            cmd.append("--previous")
        return kubectl.run("logs", *cmd) or "(no log output)"

    return handler


# ------------------------------------------------------------------------ describe


class DescribeArgs(BaseModel):
    kind: Literal[
        "pod",
        "deployment",
        "replicaset",
        "statefulset",
        "daemonset",
        "job",
        "cronjob",
        "service",
        "endpoints",
        "ingress",
        "networkpolicy",
        "resourcequota",
        "limitrange",
        "persistentvolumeclaim",
        "configmap",
        "node",
        "namespace",
    ] = Field(description="Kind of object. For configmap only key names are returned.")
    name: Name = Field(description="Object name.")
    namespace: Name | None = Field(
        default=None, description="Namespace; required except for node and namespace."
    )


def _configmap_keys(kubectl: Kubectl, namespace: str, name: str) -> str:
    """Key names and sizes only. ConfigMap values are never returned (they may hold secrets)."""
    data = _parse_json(
        kubectl.run("get", "configmap", name, f"--namespace={namespace}", "--output=json"),
        "configmap",
    )
    return _dump(
        {
            "name": name,
            "namespace": namespace,
            "keys": {k: len(str(v)) for k, v in (data.get("data") or {}).items()},
            "binary_keys": sorted((data.get("binaryData") or {}).keys()),
            "note": "values intentionally omitted",
        }
    )


def _describe(kubectl: Kubectl) -> Callable[[DescribeArgs], str]:
    def handler(args: DescribeArgs) -> str:
        if args.kind in CLUSTER_SCOPED:
            return kubectl.run("describe", DESCRIBE_KINDS[args.kind], args.name)
        if args.namespace is None:
            raise ToolError(f"namespace is required to describe a {args.kind}")
        if args.kind == "configmap":
            return _configmap_keys(kubectl, args.namespace, args.name)
        return kubectl.run(
            "describe", DESCRIBE_KINDS[args.kind], args.name, f"--namespace={args.namespace}"
        )

    return handler


# -------------------------------------------------------------------------- build


def build_kube_tools(kubectl: Kubectl, clock: Clock | None = None) -> list[Tool[Any]]:
    now: Clock = clock or (lambda: datetime.now(UTC))
    return [
        Tool(
            "list_pods",
            "List pods in a namespace with phase, readiness, restarts, container states "
            "(including last termination reason such as OOMKilled), labels and owner.",
            ListPodsArgs,
            _list_pods(kubectl, now),
        ),
        Tool(
            "get_events",
            "Recent Kubernetes events in a namespace, oldest first. Best source for why "
            "scheduling, image pulls, probes, quotas or config lookups failed.",
            GetEventsArgs,
            _get_events(kubectl),
        ),
        Tool(
            "get_logs",
            "Tail of a container's logs (secrets redacted). Use previous=true for a "
            "container that has crashed and restarted.",
            GetLogsArgs,
            _get_logs(kubectl),
        ),
        Tool(
            "describe",
            "kubectl describe for one object: spec, status, limits, probes, selectors and "
            "related events. ConfigMaps return key names only.",
            DescribeArgs,
            _describe(kubectl),
        ),
    ]


__all__ = [
    "DESCRIBE_KINDS",
    "build_kube_tools",
    "summarize_event",
    "summarize_pod",
]
