#!/usr/bin/env bash
# Render the profile to a Helm chart and install it into this worktree's cluster.
#
# The product CLI owns rendering, private temporary secret values, Helm, and
# bounded rollout checks. This wrapper supplies only worktree-specific context.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/k8s/k3d-env.sh
source "${SCRIPT_DIR}/k3d-env.sh"
cd "$CDS_REPO_ROOT"

PROFILE="${1:-profiles/local-dagster-postgres-superset/profile.yaml}"
if [[ "$#" -gt 0 ]]; then
  shift
fi
CHART_DIR="${CDS_CHART_DIR:-${CDS_REPO_ROOT}/chart}"
CDS_BIN="${CDS_BIN:-${CDS_REPO_ROOT}/.venv/bin/cds}"

echo "==> cds up --target helm ${CDS_RELEASE} into ${CDS_NAMESPACE}"
"$CDS_BIN" up "$PROFILE" \
  --target helm \
  --chart-dir "$CHART_DIR" \
  --namespace "$CDS_NAMESPACE" \
  --release "$CDS_RELEASE" \
  --kube-context "$CDS_CONTEXT" \
  --timeout 600 \
  "$@"

"${SCRIPT_DIR}/expose-local.sh"

echo
kubectl --context "$CDS_CONTEXT" -n "$CDS_NAMESPACE" get pods
