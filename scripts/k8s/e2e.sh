#!/usr/bin/env bash
# Install the real profile and prove its user-visible boundaries on local k3s.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/k8s/k3d-env.sh
source "${SCRIPT_DIR}/k3d-env.sh"

PROFILE="${1:-profiles/local-dagster-postgres-superset/profile.yaml}"
KEEP_CLUSTER="${CDS_E2E_KEEP_CLUSTER:-0}"
WAIT_SECONDS="${CDS_E2E_TIMEOUT:-600}"
E2E_PROFILE=""
CLUSTER_EXISTED=0
if k3d cluster list "$CDS_CLUSTER" >/dev/null 2>&1; then
  CLUSTER_EXISTED=1
fi
CDS_NAMESPACE="${CDS_NAMESPACE}-e2e"
CDS_RELEASE="${CDS_RELEASE}-e2e"
# NodePorts are cluster-global. The retained operator release owns the local
# host mappings while this isolated release verifies its boundaries by port-forward.
CDS_EXPOSE_LOCALHOST=0
export CDS_NAMESPACE CDS_RELEASE CDS_EXPOSE_LOCALHOST
PASS=0
FAIL=0
PORT_FORWARD_PIDS=()

record_pass() {
  PASS=$((PASS + 1))
  echo "PASS: $1"
}

record_fail() {
  FAIL=$((FAIL + 1))
  echo "FAIL: $1" >&2
}

cleanup() {
  local pid
  for pid in "${PORT_FORWARD_PIDS[@]}"; do
    kill "$pid" >/dev/null 2>&1 || true
    wait "$pid" >/dev/null 2>&1 || true
  done
  if [[ "$KEEP_CLUSTER" != "1" ]]; then
    helm --kube-context "$CDS_CONTEXT" uninstall "$CDS_RELEASE" \
      --namespace "$CDS_NAMESPACE" --ignore-not-found >/dev/null 2>&1 || true
    kubectl --context "$CDS_CONTEXT" delete namespace "$CDS_NAMESPACE" \
      --ignore-not-found --wait=false >/dev/null 2>&1 || true
    if [[ "$CLUSTER_EXISTED" == "0" ]]; then
      "${SCRIPT_DIR}/k3d-down.sh" >/dev/null 2>&1 || true
    fi
  fi
  if [[ -n "$E2E_PROFILE" ]]; then
    rm -f "$E2E_PROFILE"
  fi
  echo "=== CDS-K8S-E2E DONE pass=${PASS} fail=${FAIL} ==="
}
trap cleanup EXIT
trap 'exit 130' INT TERM

run_setup() {
  local label="$1"
  shift
  if "$@"; then
    record_pass "$label"
  else
    record_fail "$label"
    return 1
  fi
}

wait_for_http() {
  local url="$1"
  local pattern="$2"
  local deadline=$((SECONDS + 90))
  local body
  while (( SECONDS < deadline )); do
    body="$(curl --fail --silent --show-error --max-time 5 "$url" 2>/dev/null || true)"
    if grep -Eqi "$pattern" <<<"$body"; then
      return 0
    fi
    sleep 2
  done
  return 1
}

PROFILE_DIR="$(cd "$(dirname "$PROFILE")" && pwd)"
PROFILE="${PROFILE_DIR}/$(basename "$PROFILE")"
E2E_PROFILE="$(mktemp "${PROFILE_DIR}/.cds-e2e-profile.XXXXXX.yaml")"
if ! "${CDS_REPO_ROOT}/.venv/bin/python" \
  "${SCRIPT_DIR}/clusterip_profile.py" "$PROFILE" "$E2E_PROFILE"; then
  record_fail "isolated ClusterIP profile"
  exit 1
fi

if ! run_setup "cluster available" "${SCRIPT_DIR}/k3d-up.sh"; then
  exit 1
fi
if ! run_setup "Helm release installed" "${SCRIPT_DIR}/install.sh" "$E2E_PROFILE"; then
  exit 1
fi

if kubectl --context "$CDS_CONTEXT" -n "$CDS_NAMESPACE" wait \
    --for=condition=Available deployment --all --timeout="${WAIT_SECONDS}s" \
  && kubectl --context "$CDS_CONTEXT" -n "$CDS_NAMESPACE" wait \
    --for=jsonpath='{.status.readyReplicas}'=1 "statefulset/${CDS_RELEASE}-postgres" \
    --timeout="${WAIT_SECONDS}s"; then
  record_pass "all workloads became Ready within ${WAIT_SECONDS}s"
else
  record_fail "workloads did not become Ready within ${WAIT_SECONDS}s"
fi

if kubectl --context "$CDS_CONTEXT" -n "$CDS_NAMESPACE" exec "${CDS_RELEASE}-postgres-0" -- \
  sh -ceu 'export PGPASSWORD="$POSTGRES_PASSWORD"; for db in "$DAGSTER_DB_NAME" "$SUPERSET_DB_NAME" "$ANALYTICS_DB_NAME"; do psql -U "$POSTGRES_USER" -d postgres -Atqc "SELECT 1 FROM pg_database WHERE datname='"'"'$db'"'"'" | grep -qx 1; done'; then
  record_pass "PostgreSQL accepts connections and contains all expected databases"
else
  record_fail "PostgreSQL database boundary"
fi

kubectl --context "$CDS_CONTEXT" -n "$CDS_NAMESPACE" port-forward \
  service/dagster-webserver "${CDS_DAGSTER_FORWARD_PORT}:3000" \
  >"/tmp/${CDS_CLUSTER}-${CDS_RELEASE}-dagster-port-forward.log" 2>&1 &
PORT_FORWARD_PIDS+=("$!")
if wait_for_http "http://127.0.0.1:${CDS_DAGSTER_FORWARD_PORT}/" 'dagster|graphql'; then
  record_pass "Dagster serves HTTP"
else
  record_fail "Dagster HTTP boundary"
fi

kubectl --context "$CDS_CONTEXT" -n "$CDS_NAMESPACE" port-forward \
  service/superset "${CDS_SUPERSET_FORWARD_PORT}:8088" \
  >"/tmp/${CDS_CLUSTER}-${CDS_RELEASE}-superset-port-forward.log" 2>&1 &
PORT_FORWARD_PIDS+=("$!")
if wait_for_http "http://127.0.0.1:${CDS_SUPERSET_FORWARD_PORT}/login/" 'login|superset'; then
  record_pass "Superset serves its login page"
else
  record_fail "Superset login boundary"
fi

if (( FAIL > 0 )); then
  exit 1
fi
