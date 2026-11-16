#!/usr/bin/env bash
# Apply the read-only RBAC and write a kubeconfig that authenticates as the read-only
# ServiceAccount, using a short-lived token. The agent must only ever use this kubeconfig,
# never your admin one.
#   scripts/agent-kubeconfig.sh            # writes .kube/agent.kubeconfig (gitignored)
#   OUT=/tmp/agent.kubeconfig DURATION=2h scripts/agent-kubeconfig.sh
set -euo pipefail

cd "$(dirname "$0")/.."

OUT="${OUT:-.kube/agent.kubeconfig}"
DURATION="${DURATION:-8h}"
NAMESPACE="sre-agent"
SERVICE_ACCOUNT="sre-agent-readonly"

command -v kubectl >/dev/null 2>&1 || {
  echo "error: 'kubectl' is required but not installed" >&2
  exit 1
}

kubectl apply -f lab/rbac/readonly.yaml

server="$(kubectl config view --minify --raw -o jsonpath='{.clusters[0].cluster.server}')"
ca_data="$(kubectl config view --minify --raw -o jsonpath='{.clusters[0].cluster.certificate-authority-data}')"
if [[ -z "$server" || -z "$ca_data" ]]; then
  echo "error: current context must have a server and inline certificate-authority-data" >&2
  exit 1
fi

token="$(kubectl --namespace "$NAMESPACE" create token "$SERVICE_ACCOUNT" --duration "$DURATION")"

mkdir -p "$(dirname "$OUT")"
umask 077
cat >"$OUT" <<KUBECONFIG
apiVersion: v1
kind: Config
clusters:
  - name: lab
    cluster:
      server: ${server}
      certificate-authority-data: ${ca_data}
users:
  - name: ${SERVICE_ACCOUNT}
    user:
      token: ${token}
contexts:
  - name: lab-readonly
    context:
      cluster: lab
      user: ${SERVICE_ACCOUNT}
current-context: lab-readonly
KUBECONFIG
chmod 600 "$OUT"

echo "wrote $OUT (token valid for ${DURATION}); sanity check:"
echo "  kubectl --kubeconfig $OUT auth can-i get pods --all-namespaces      # yes"
echo "  kubectl --kubeconfig $OUT auth can-i get secrets --all-namespaces   # no"
echo "  kubectl --kubeconfig $OUT auth can-i delete pods --all-namespaces   # no"
