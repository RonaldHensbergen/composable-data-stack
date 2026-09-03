#!/usr/bin/env bash
# Deterministic, per-worktree names and ports for the local k3s cluster.
#
# Sourced by every other script here. Everything is derived from the git branch,
# so the same branch gets the same cluster and the same URLs on every run, and
# two worktrees checked out at different branches never collide.
#
# KUBECONFIG is exported to a per-worktree file on purpose. `kubectl config
# use-context` writes to the shared ~/.kube/config and races other worktrees, so
# no script here may call it.
set -euo pipefail

CDS_REPO_ROOT="$(git rev-parse --show-toplevel)"
CDS_BRANCH="$(git -C "$CDS_REPO_ROOT" rev-parse --abbrev-ref HEAD)"

# Slug the branch into something a cluster name and a DNS label both accept.
CDS_SLUG="$(printf '%s' "$CDS_BRANCH" | tr '[:upper:]' '[:lower:]' | sed 's#[^a-z0-9]#-#g; s#--*#-#g; s#^-##; s#-$##')"
CDS_SLUG="${CDS_SLUG:0:24}"

# base = 20000 + (sha1(branch) mod 1900) * 20, matching the worktree convention.
_hash="$(printf '%s' "$CDS_BRANCH" | shasum | cut -c1-8)"
_dec="$((16#${_hash}))"
CDS_PORT_BASE="$(( 20000 + (_dec % 1900) * 20 ))"

CDS_CLUSTER="${CDS_CLUSTER:-cds-${CDS_SLUG}}"
CDS_CONTEXT="${CDS_CONTEXT:-k3d-${CDS_CLUSTER}}"
CDS_NAMESPACE="${CDS_NAMESPACE:-cds-local}"
CDS_RELEASE="${CDS_RELEASE:-cds}"

CDS_API_PORT="$(( CDS_PORT_BASE + 0 ))"
CDS_LB_PORT="$(( CDS_PORT_BASE + 1 ))"
CDS_DAGSTER_PORT="$(( CDS_PORT_BASE + 2 ))"
CDS_SUPERSET_PORT="$(( CDS_PORT_BASE + 3 ))"
CDS_DAGSTER_FORWARD_PORT="$(( CDS_PORT_BASE + 4 ))"
CDS_SUPERSET_FORWARD_PORT="$(( CDS_PORT_BASE + 5 ))"

CDS_KUBECONFIG="${CDS_REPO_ROOT}/.k3d/${CDS_CLUSTER}.kubeconfig"
export KUBECONFIG="$CDS_KUBECONFIG"

# Images the profile builds locally. They are imported into the node rather than
# pulled, and the manifests set imagePullPolicy: Never to match.
CDS_LOCAL_IMAGES=("local/dagster:custom" "local/superset:custom")

cds_require_free_ports() {
  local label port
  while [[ "$#" -gt 0 ]]; do
    label="$1"
    port="$2"
    shift 2
    if lsof -nP -iTCP:"${port}" -sTCP:LISTEN >/dev/null 2>&1; then
      echo "ERROR ${label} port ${port} is already in use." >&2
      echo "      Stop that listener or use a different branch name for another deterministic port set." >&2
      return 1
    fi
  done
}

cds_k3d_summary() {
  cat <<EOF
branch      : ${CDS_BRANCH}
cluster     : ${CDS_CLUSTER}
context     : ${CDS_CONTEXT}
namespace   : ${CDS_NAMESPACE}
kubeconfig  : ${CDS_KUBECONFIG}
api port    : ${CDS_API_PORT}
node ports  : ${CDS_DAGSTER_PORT} (Dagster), ${CDS_SUPERSET_PORT} (Superset)
port-forward: ${CDS_DAGSTER_FORWARD_PORT} (Dagster), ${CDS_SUPERSET_FORWARD_PORT} (Superset)
EOF
}
