#!/usr/bin/env bash
# Verify the rendered UI NodePorts published by this k3d cluster.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/k8s/k3d-env.sh
source "${SCRIPT_DIR}/k3d-env.sh"

case "${CDS_EXPOSE_LOCALHOST:-1}" in
  0)
    echo "==> localhost exposure disabled (CDS_EXPOSE_LOCALHOST=0)"
    exit 0
    ;;
  1) ;;
  *)
    echo "ERROR CDS_EXPOSE_LOCALHOST must be 0 or 1." >&2
    exit 2
    ;;
esac

verify_local_service() {
  local service_name="$1"
  local node_port="$2"
  local actual

  actual="$(kubectl --context "$CDS_CONTEXT" --namespace "$CDS_NAMESPACE" get \
    service "$service_name" -o jsonpath='{.spec.type}:{.spec.ports[?(@.name=="http")].nodePort}')"
  if [[ "$actual" != "NodePort:${node_port}" ]]; then
    echo "ERROR ${service_name} did not acquire NodePort ${node_port}." >&2
    return 1
  fi
}

echo "==> exposing local UIs through k3d"
verify_local_service dagster-webserver 30300
verify_local_service superset 30808

cat <<EOF
Dagster: http://127.0.0.1:${CDS_DAGSTER_PORT}
Superset: http://127.0.0.1:${CDS_SUPERSET_PORT}
EOF
