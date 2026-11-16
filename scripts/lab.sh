#!/usr/bin/env bash
# Create or destroy the local lab cluster (kind + demo app + kube-prometheus-stack).
set -euo pipefail

CLUSTER="${CLUSTER:-sre-agent}"
cd "$(dirname "$0")/.."

need() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "error: '$1' is required but not installed" >&2
    exit 1
  }
}

up() {
  need kind
  need kubectl
  need helm

  if ! kind get clusters | grep -qx "$CLUSTER"; then
    kind create cluster --name "$CLUSTER" --config lab/kind-config.yaml
  fi
  kubectl config use-context "kind-$CLUSTER"

  kubectl apply -f lab/demo-app/namespace.yaml
  kubectl apply -f lab/demo-app/podinfo.yaml

  helm repo add prometheus-community https://prometheus-community.github.io/helm-charts >/dev/null
  helm repo update >/dev/null
  helm upgrade --install monitoring prometheus-community/kube-prometheus-stack \
    --namespace monitoring --create-namespace \
    --values lab/monitoring-values.yaml \
    --wait --timeout 10m

  kubectl -n demo rollout status deploy/podinfo --timeout=120s
  echo "lab ready: context kind-$CLUSTER"
}

down() {
  need kind
  kind delete cluster --name "$CLUSTER"
}

case "${1:-}" in
  up) up ;;
  down) down ;;
  *)
    echo "usage: $0 up|down" >&2
    exit 2
    ;;
esac
