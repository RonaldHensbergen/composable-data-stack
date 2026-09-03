#!/usr/bin/env bash
# Create (or reuse) this worktree's local k3s cluster and load the profile's
# locally built images into it.
#
# Only ever touches the cluster named after this branch: no `k3d cluster delete
# --all`, no `docker prune`, nothing that reaches a sibling worktree.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/k8s/k3d-env.sh
source "${SCRIPT_DIR}/k3d-env.sh"

mkdir -p "$(dirname "$CDS_KUBECONFIG")"

if k3d cluster list "$CDS_CLUSTER" >/dev/null 2>&1; then
  echo "==> cluster ${CDS_CLUSTER} already exists"
else
  echo "==> creating cluster ${CDS_CLUSTER}"
  cds_require_free_ports \
    api "$CDS_API_PORT" \
    load-balancer "$CDS_LB_PORT" \
    dagster "$CDS_DAGSTER_PORT" \
    superset "$CDS_SUPERSET_PORT"
  k3d cluster create "$CDS_CLUSTER" \
    --api-port "127.0.0.1:${CDS_API_PORT}" \
    --port "${CDS_DAGSTER_PORT}:30300@server:0" \
    --port "${CDS_SUPERSET_PORT}:30808@server:0" \
    --k3s-arg "--disable=traefik@server:0" \
    --wait
fi

k3d kubeconfig get "$CDS_CLUSTER" > "$CDS_KUBECONFIG"
chmod 600 "$CDS_KUBECONFIG"

echo "==> waiting for the node to be Ready"
kubectl --context "$CDS_CONTEXT" wait --for=condition=Ready node --all --timeout=120s

# A node under disk pressure taints itself and every Pod stays Pending with no
# obvious cause, so say so plainly rather than letting the deploy hang.
if kubectl --context "$CDS_CONTEXT" get nodes -o jsonpath='{.items[*].spec.taints[*].key}' \
   | tr ' ' '\n' | grep -q 'disk-pressure'; then
  echo "ERROR node carries a disk-pressure taint. Free space in the Docker VM" >&2
  echo "      (docker builder prune -af; docker image prune -af), then run:" >&2
  echo "      docker restart k3d-${CDS_CLUSTER}-server-0" >&2
  exit 1
fi

echo "==> importing locally built images"
for image in "${CDS_LOCAL_IMAGES[@]}"; do
  if docker image inspect "$image" >/dev/null 2>&1; then
    echo "    $image"
    k3d image import "$image" --cluster "$CDS_CLUSTER" >/dev/null
  else
    echo "    $image NOT BUILT (run scripts/k8s/build-images.sh first)" >&2
  fi
done

kubectl --context "$CDS_CONTEXT" create namespace "$CDS_NAMESPACE" \
  --dry-run=client -o yaml | kubectl --context "$CDS_CONTEXT" apply -f - >/dev/null

echo
cds_k3d_summary
