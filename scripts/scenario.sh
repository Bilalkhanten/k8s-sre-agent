#!/usr/bin/env bash
# Inject or remove one failure scenario in the lab cluster.
#   scripts/scenario.sh up   <scenario-id>
#   scripts/scenario.sh down <scenario-id>
set -euo pipefail

cd "$(dirname "$0")/.."

action="${1:-}"
id="${2:-}"
dir="scenarios/${id}"

if [[ -z "$action" || -z "$id" || ! -d "$dir" ]]; then
  echo "usage: $0 up|down <scenario-id>   (see: k8s-sre-agent scenarios list)" >&2
  exit 2
fi

case "$action" in
  up)
    if [[ -f "$dir/setup.yaml" ]]; then
      kubectl apply -f "$dir/setup.yaml"
      kubectl wait --for=condition=available deployment \
        --selector "sre-agent.dev/scenario=${id}" --all-namespaces --timeout=180s
    fi
    kubectl apply -f "$dir/manifest.yaml"
    echo "scenario '${id}' injected"
    ;;
  down)
    kubectl delete -f "$dir/manifest.yaml" --ignore-not-found
    if [[ -f "$dir/setup.yaml" ]]; then
      kubectl delete -f "$dir/setup.yaml" --ignore-not-found
    fi
    echo "scenario '${id}' removed"
    ;;
  *)
    echo "usage: $0 up|down <scenario-id>" >&2
    exit 2
    ;;
esac
