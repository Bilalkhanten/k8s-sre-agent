"""Load and validate the failure scenarios shipped in ``scenarios/``.

A scenario is a directory containing:

* ``manifest.yaml``  - injects the fault into the lab cluster
* ``setup.yaml``     - optional; applied first and waited on (for faults that need a healthy
                       "before" state, e.g. a bad rollout)
* ``alert.json``     - the alert the agent receives as input
* ``expected.json``  - ground truth used by the scorer (root cause, evidence, red herrings)

Every object must carry the ``sre-agent.dev/scenario: <id>`` label so a scenario can be torn
down cleanly, and every namespaced object must set ``metadata.namespace`` explicitly so the
scenario behaves the same regardless of the caller's kubectl context.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, ValidationError

SCENARIO_LABEL = "sre-agent.dev/scenario"
CLUSTER_SCOPED_KINDS = {"Namespace"}

Difficulty = Literal["easy", "medium", "hard"]
Category = Literal["resource-limits", "image", "probe", "quota", "network", "configuration"]


class ScenarioError(ValueError):
    """Raised when a scenario directory is malformed."""


class ResourceRef(BaseModel):
    kind: str
    name: str
    namespace: str


class RootCause(BaseModel):
    category: Category
    summary: str = Field(min_length=10)
    resource: ResourceRef


class Expected(BaseModel):
    """Ground truth for one scenario (``expected.json``)."""

    id: str
    title: str
    difficulty: Difficulty
    root_cause: RootCause
    required_evidence: list[str] = Field(min_length=1)
    red_herrings: list[str] = Field(default_factory=list)
    fix: str = Field(min_length=10)


class Scenario(BaseModel):
    id: str
    path: Path
    expected: Expected
    alert: dict[str, Any]
    manifest: Path
    setup_manifest: Path | None = None


def _read_docs(path: Path, scenario_id: str) -> list[dict[str, Any]]:
    """Parse a multi-document manifest and enforce the scenario conventions."""
    try:
        docs = [d for d in yaml.safe_load_all(path.read_text()) if d is not None]
    except yaml.YAMLError as exc:
        raise ScenarioError(f"{path}: invalid YAML: {exc}") from exc

    if not docs:
        raise ScenarioError(f"{path}: contains no Kubernetes objects")

    for doc in docs:
        if not isinstance(doc, dict) or "apiVersion" not in doc or "kind" not in doc:
            raise ScenarioError(f"{path}: every document needs apiVersion and kind")
        meta = doc.get("metadata") or {}
        name = meta.get("name")
        if not name:
            raise ScenarioError(f"{path}: {doc['kind']} is missing metadata.name")
        if doc["kind"] not in CLUSTER_SCOPED_KINDS and not meta.get("namespace"):
            raise ScenarioError(f"{path}: {doc['kind']}/{name} must set metadata.namespace")
        if (meta.get("labels") or {}).get(SCENARIO_LABEL) != scenario_id:
            raise ScenarioError(
                f"{path}: {doc['kind']}/{name} must carry label {SCENARIO_LABEL}={scenario_id}"
            )
    return docs


def _contains(docs: list[dict[str, Any]], ref: ResourceRef) -> bool:
    return any(
        d["kind"] == ref.kind
        and d["metadata"]["name"] == ref.name
        and d["metadata"].get("namespace", ref.namespace) == ref.namespace
        for d in docs
    )


def load_scenario(path: Path) -> Scenario:
    """Load and validate a single scenario directory."""
    scenario_id = path.name
    files = {name: path / name for name in ("expected.json", "alert.json", "manifest.yaml")}
    for name, file in files.items():
        if not file.is_file():
            raise ScenarioError(f"{path}: missing {name}")

    try:
        expected = Expected.model_validate_json(files["expected.json"].read_text())
    except ValidationError as exc:
        raise ScenarioError(f"{files['expected.json']}: {exc}") from exc
    if expected.id != scenario_id:
        raise ScenarioError(f"{path}: expected.json id {expected.id!r} != directory name")

    try:
        alert = json.loads(files["alert.json"].read_text())
    except json.JSONDecodeError as exc:
        raise ScenarioError(f"{files['alert.json']}: invalid JSON: {exc}") from exc
    if not isinstance(alert, dict) or "alertname" not in (alert.get("labels") or {}):
        raise ScenarioError(f"{files['alert.json']}: needs labels.alertname")

    docs = _read_docs(files["manifest.yaml"], scenario_id)
    setup_path = path / "setup.yaml"
    setup_docs: list[dict[str, Any]] = []
    if setup_path.is_file():
        setup_docs = _read_docs(setup_path, scenario_id)

    if not _contains(docs + setup_docs, expected.root_cause.resource):
        ref = expected.root_cause.resource
        raise ScenarioError(
            f"{path}: root-cause resource {ref.kind}/{ref.name} ({ref.namespace}) "
            "is not defined in manifest.yaml or setup.yaml"
        )

    return Scenario(
        id=scenario_id,
        path=path,
        expected=expected,
        alert=alert,
        manifest=files["manifest.yaml"],
        setup_manifest=setup_path if setup_path.is_file() else None,
    )


def load_scenarios(root: Path) -> list[Scenario]:
    """Load every scenario under ``root`` (sorted by id)."""
    if not root.is_dir():
        raise ScenarioError(f"{root}: scenarios directory not found")
    dirs = sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith((".", "_")))
    return [load_scenario(p) for p in dirs]
