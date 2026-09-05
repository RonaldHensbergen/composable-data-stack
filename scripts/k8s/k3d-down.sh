#!/usr/bin/env bash
# Delete only this worktree's cluster. Never --all.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/k8s/k3d-env.sh
source "${SCRIPT_DIR}/k3d-env.sh"

if k3d cluster list "$CDS_CLUSTER" >/dev/null 2>&1; then
  echo "==> deleting cluster ${CDS_CLUSTER}"
  k3d cluster delete "$CDS_CLUSTER"
else
  echo "==> cluster ${CDS_CLUSTER} does not exist"
fi
rm -f "$CDS_KUBECONFIG"
