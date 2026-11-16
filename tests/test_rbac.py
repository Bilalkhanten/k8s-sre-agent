"""The agent's cluster access is read-only by RBAC. These tests pin that down statically."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

RBAC_FILE = Path(__file__).resolve().parent.parent / "lab" / "rbac" / "readonly.yaml"
READ_VERBS = {"get", "list", "watch"}
SECRET_LIKE = {"secrets", "serviceaccounts/token", "pods/exec", "pods/attach", "pods/portforward"}


@pytest.fixture(scope="module")
def docs() -> dict[str, dict[str, Any]]:
    loaded = [d for d in yaml.safe_load_all(RBAC_FILE.read_text()) if d]
    return {d["kind"]: d for d in loaded}


def test_expected_objects_exist(docs: dict[str, dict[str, Any]]) -> None:
    assert set(docs) == {"Namespace", "ServiceAccount", "ClusterRole", "ClusterRoleBinding"}


def test_only_read_verbs_are_granted(docs: dict[str, dict[str, Any]]) -> None:
    for rule in docs["ClusterRole"]["rules"]:
        assert set(rule["verbs"]) <= READ_VERBS, rule


def test_no_wildcards(docs: dict[str, dict[str, Any]]) -> None:
    for rule in docs["ClusterRole"]["rules"]:
        for field in ("apiGroups", "resources", "verbs"):
            assert "*" not in rule[field], (field, rule)
        assert "resourceNames" not in rule or "*" not in rule["resourceNames"]


def test_secrets_and_exec_are_not_readable(docs: dict[str, dict[str, Any]]) -> None:
    granted = {r for rule in docs["ClusterRole"]["rules"] for r in rule["resources"]}
    assert not granted & SECRET_LIKE


def test_binding_targets_the_service_account(docs: dict[str, dict[str, Any]]) -> None:
    binding = docs["ClusterRoleBinding"]
    sa = docs["ServiceAccount"]["metadata"]
    assert binding["roleRef"]["name"] == docs["ClusterRole"]["metadata"]["name"]
    assert binding["subjects"] == [
        {"kind": "ServiceAccount", "name": sa["name"], "namespace": sa["namespace"]}
    ]
