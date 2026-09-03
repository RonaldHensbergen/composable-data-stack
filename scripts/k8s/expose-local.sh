#!/usr/bin/env bash
# Attach the local UI Services to the NodePorts published by this k3d cluster.
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

patch_local_service() {
  local service_name="$1"
  local service_port="$2"
  local target_port="$3"
  local node_port="$4"
  local payload actual

  payload="$(printf '{"spec":{"type":"NodePort","ports":[{"name":"http","port":%s,"targetPort":%s,"protocol":"TCP","nodePort":%s}]}}' \
    "$service_port" "$target_port" "$node_port")"
  kubectl --context "$CDS_CONTEXT" --namespace "$CDS_NAMESPACE" patch \
    service "$service_name" --type=merge --patch "$payload" >/dev/null

  actual="$(kubectl --context "$CDS_CONTEXT" --namespace "$CDS_NAMESPACE" get \
    service "$service_name" -o jsonpath='{.spec.type}:{.spec.ports[?(@.name=="http")].nodePort}')"
  if [[ "$actual" != "NodePort:${node_port}" ]]; then
    echo "ERROR ${service_name} did not acquire NodePort ${node_port}." >&2
    return 1
  fi
}

echo "==> exposing local UIs through k3d"
patch_local_service dagster-webserver 3000 3000 30300
patch_local_service superset 8088 8088 30808

cat <<EOF
Dagster: http://127.0.0.1:${CDS_DAGSTER_PORT}
Superset: http://127.0.0.1:${CDS_SUPERSET_PORT}
EOF
