# Lab

A disposable Kubernetes cluster for reproducing incidents.

## Requirements
[`kind`](https://kind.sigs.k8s.io/), `kubectl`, `helm`, Docker.

## Usage
```bash
make lab-up                          # kind cluster + demo app + kube-prometheus-stack
make scenario-up S=oom-killed        # inject a fault
kubectl -n demo get pods             # watch it break
make scenario-down S=oom-killed      # clean up
make lab-down                        # delete the cluster
```

## What gets installed
- `kind-config.yaml` - 1 control plane + 2 workers
- `demo-app/` - a healthy podinfo workload in namespace `demo`, so the cluster has realistic
  background noise
- `monitoring-values.yaml` - slim kube-prometheus-stack (Prometheus, Alertmanager, Grafana;
  Grafana admin password is `admin`, lab only)

## NetworkPolicy scenario
`networkpolicy-block` only reproduces if the cluster CNI enforces NetworkPolicy. If traffic is
not blocked on your kind version, recreate the cluster with the default CNI disabled
(`networking.disableDefaultCNI: true` in `kind-config.yaml`) and install Calico.
