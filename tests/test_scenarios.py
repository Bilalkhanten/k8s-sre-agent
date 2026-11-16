from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from k8s_sre_agent.cli import main
from k8s_sre_agent.scenarios import ScenarioError, load_scenario, load_scenarios

SCENARIOS_ROOT = Path(__file__).resolve().parent.parent / "scenarios"

EXPECTED_IDS = {
    "oom-killed",
    "image-pull-backoff",
    "failing-readiness-probe",
    "quota-exceeded",
    "networkpolicy-block",
    "bad-config-rollout",
}


@pytest.fixture
def scenario_copy(tmp_path: Path) -> Path:
    """A throwaway copy of a real scenario that tests can break on purpose."""
    dest = tmp_path / "oom-killed"
    shutil.copytree(SCENARIOS_ROOT / "oom-killed", dest)
    return dest


def test_shipped_scenarios_are_valid() -> None:
    scenarios = load_scenarios(SCENARIOS_ROOT)
    assert {s.id for s in scenarios} == EXPECTED_IDS


def test_setup_manifest_detected() -> None:
    by_id = {s.id: s for s in load_scenarios(SCENARIOS_ROOT)}
    assert by_id["bad-config-rollout"].setup_manifest is not None
    assert by_id["oom-killed"].setup_manifest is None


def test_every_scenario_has_evidence_and_a_fix() -> None:
    for s in load_scenarios(SCENARIOS_ROOT):
        assert s.expected.required_evidence, s.id
        assert s.expected.fix, s.id


def test_missing_label_is_rejected(scenario_copy: Path) -> None:
    manifest = scenario_copy / "manifest.yaml"
    manifest.write_text(manifest.read_text().replace("sre-agent.dev/scenario", "other/label"))
    with pytest.raises(ScenarioError, match="must carry label"):
        load_scenario(scenario_copy)


def test_missing_namespace_is_rejected(scenario_copy: Path) -> None:
    manifest = scenario_copy / "manifest.yaml"
    manifest.write_text(manifest.read_text().replace("  namespace: demo\n", "", 1))
    with pytest.raises(ScenarioError, match="must set metadata.namespace"):
        load_scenario(scenario_copy)


def test_id_must_match_directory(scenario_copy: Path) -> None:
    expected = scenario_copy / "expected.json"
    data = json.loads(expected.read_text())
    data["id"] = "something-else"
    expected.write_text(json.dumps(data))
    with pytest.raises(ScenarioError, match="!= directory name"):
        load_scenario(scenario_copy)


def test_root_cause_resource_must_exist(scenario_copy: Path) -> None:
    expected = scenario_copy / "expected.json"
    data = json.loads(expected.read_text())
    data["root_cause"]["resource"]["name"] = "does-not-exist"
    expected.write_text(json.dumps(data))
    with pytest.raises(ScenarioError, match="root-cause resource"):
        load_scenario(scenario_copy)


def test_evidence_is_required(scenario_copy: Path) -> None:
    expected = scenario_copy / "expected.json"
    data = json.loads(expected.read_text())
    data["required_evidence"] = []
    expected.write_text(json.dumps(data))
    with pytest.raises(ScenarioError):
        load_scenario(scenario_copy)


def test_missing_file_is_reported(scenario_copy: Path) -> None:
    (scenario_copy / "alert.json").unlink()
    with pytest.raises(ScenarioError, match="missing alert.json"):
        load_scenario(scenario_copy)


def test_cli_list(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["scenarios", "list", "--root", str(SCENARIOS_ROOT)]) == 0
    out = capsys.readouterr().out
    for scenario_id in EXPECTED_IDS:
        assert scenario_id in out


def test_cli_validate_reports_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "broken").mkdir()
    assert main(["scenarios", "validate", "--root", str(tmp_path)]) == 1
    assert "missing" in capsys.readouterr().err
