# k8s-sre-agent

AI incident investigator for Kubernetes: an agent receives an alert, investigates a cluster with
read-only tools, and returns a structured root-cause report. An eval harness scores it against
ground truth in `scenarios/`.

## Commands
- `make install` - `uv sync`
- `make check` - ruff, pytest and scenario validation (run before every commit)
- `make lab-up` / `make lab-down` - local kind cluster + demo app + monitoring
- `make scenario-up S=<id>` / `make scenario-down S=<id>` - inject / remove a failure

## Conventions
- Python 3.11+, type hints, `src/` layout, ruff (line length 100), pytest.
- Every scenario lives in `scenarios/<id>/` with `manifest.yaml`, `alert.json`, `expected.json`
  and optional `setup.yaml`. All objects carry label `sre-agent.dev/scenario: <id>` and set
  `metadata.namespace`. `k8s-sre-agent scenarios validate` enforces this.
- New behaviour needs a test or a scenario.

## Safety rules (non-negotiable)
- Anything that touches a cluster is **read-only by default**. Enforce it with RBAC, not just
  prompts.
- No secrets in logs, prompts or reports; redact before sending to a model.
- Agent runs are bounded (steps, tokens) and every tool call is audit-logged.
