# k8s-sre-agent

[![ci](https://github.com/Bilalkhanten/k8s-sre-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/Bilalkhanten/k8s-sre-agent/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

An open-source **AI incident investigator for Kubernetes**. Give it an alert; it investigates the
cluster with **read-only tools** and returns a structured root-cause report with linked evidence.
Every behaviour is **scored against ground truth** in a reproducible failure lab, so claims about
the agent can be measured rather than assumed.

> **Status: early / pre-alpha.** The failure lab and scenario format are done. The tools layer,
> agent loop and scorer are next - see the [roadmap](#roadmap).

## Why

AI SRE demos are easy; trustworthy ones are not. This project focuses on the parts that make an
incident agent safe to put near production:

- **Evals first** - six realistic failures with expected root cause, required evidence and
  red herrings, so accuracy is a number.
- **Safety by construction** - read-only RBAC, bounded steps and an audit log, not "please don't
  delete anything" in a prompt.
- **Reproducible** - one command builds the lab; one command injects a fault.

## Architecture

```mermaid
flowchart LR
    A[Alert JSON] --> B[Agent loop<br/>bounded steps]
    B <--> C[Read-only tools<br/>kubectl / Prometheus / logs]
    C --> D[(kind lab cluster<br/>injected failures)]
    B --> E[Structured report<br/>root cause + evidence]
    E --> F[Scorer]
    G[expected.json] --> F
```

## Quickstart

Requires Python 3.11+, [`uv`](https://docs.astral.sh/uv/), and for the lab: Docker, `kind`,
`kubectl`, `helm`.

```bash
git clone https://github.com/Bilalkhanten/k8s-sre-agent && cd k8s-sre-agent
make install
make check                      # lint, tests, scenario validation

make lab-up                     # kind cluster + demo app + monitoring
make scenarios-list
make scenario-up S=oom-killed   # inject a fault
kubectl -n demo get pods        # watch it break
make scenario-down S=oom-killed
make lab-down
```

## Scenarios

| ID | Difficulty | Failure |
|---|---|---|
| `oom-killed` | easy | Container exceeds its memory limit and is OOMKilled |
| `image-pull-backoff` | easy | Deployment references an image tag that does not exist |
| `failing-readiness-probe` | medium | Readiness probe hits a 404 path; Service has no endpoints |
| `quota-exceeded` | medium | ReplicaSet cannot create pods because the namespace quota is full |
| `networkpolicy-block` | hard | A NetworkPolicy silently drops traffic from the calling service |
| `bad-config-rollout` | hard | New revision references a missing ConfigMap key; rollout stalls |

Each scenario is a directory with `manifest.yaml` (the fault), `alert.json` (agent input),
`expected.json` (ground truth) and an optional `setup.yaml`. Conventions are enforced by
`k8s-sre-agent scenarios validate`. Adding a scenario is the easiest way to contribute.

## Roadmap

- [x] Project scaffold, CI, scenario format and validator
- [x] Local lab (kind + demo app + Prometheus/Alertmanager) and six failure scenarios
- [ ] Read-only tools layer (kubectl, events, logs, PromQL) under a read-only ServiceAccount
- [ ] Agent loop with structured report output
- [ ] Eval harness: score root cause, evidence and red-herring handling; publish results here
- [ ] Guardrails: step/token budgets, secret redaction, audit log, dry-run
- [ ] Alertmanager webhook receiver and deployment guide

## Safety principles

1. Cluster access is read-only by default, enforced by Kubernetes RBAC.
2. Secrets are redacted before anything reaches a model.
3. Every run is bounded and every tool call is logged.

## License

[Apache-2.0](LICENSE)
