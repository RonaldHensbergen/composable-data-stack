#!/usr/bin/env bash
# Install the real profile and prove its user-visible boundaries on local k3s.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/k8s/k3d-env.sh
source "${SCRIPT_DIR}/k3d-env.sh"

PROFILE="${1:-profiles/local-kubernetes-example/profile.yaml}"
KEEP_CLUSTER="${CDS_E2E_KEEP_CLUSTER:-0}"
WAIT_SECONDS="${CDS_E2E_TIMEOUT:-600}"
ISTIO_VERSION="${ISTIO_VERSION:-1.31.3}"
ISTIO_NAMESPACE="istio-system"
ISTIO_BASE_RELEASE="${CDS_CLUSTER}-istio-base"
ISTIOD_RELEASE="${CDS_CLUSTER}-istiod"
E2E_PROFILE=""
CLUSTER_EXISTED=0
ISTIO_INSTALL_STARTED=0
ISTIO_NAMESPACE_CREATED=0
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
    if [[ "$ISTIO_INSTALL_STARTED" == "1" ]]; then
      helm --kube-context "$CDS_CONTEXT" uninstall "$ISTIOD_RELEASE" \
        --namespace "$ISTIO_NAMESPACE" --ignore-not-found >/dev/null 2>&1 || true
      helm --kube-context "$CDS_CONTEXT" uninstall "$ISTIO_BASE_RELEASE" \
        --namespace "$ISTIO_NAMESPACE" --ignore-not-found >/dev/null 2>&1 || true
      if [[ "$ISTIO_NAMESPACE_CREATED" == "1" ]]; then
        kubectl --context "$CDS_CONTEXT" delete namespace "$ISTIO_NAMESPACE" \
          --ignore-not-found --wait=false >/dev/null 2>&1 || true
      fi
    fi
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

install_istio() {
  local base_exists=0
  local istiod_exists=0
  if helm --kube-context "$CDS_CONTEXT" status "$ISTIO_BASE_RELEASE" \
      --namespace "$ISTIO_NAMESPACE" >/dev/null 2>&1; then
    base_exists=1
  fi
  if helm --kube-context "$CDS_CONTEXT" status "$ISTIOD_RELEASE" \
      --namespace "$ISTIO_NAMESPACE" >/dev/null 2>&1; then
    istiod_exists=1
  fi
  if [[ "$base_exists" != "$istiod_exists" ]]; then
    echo "ERROR only one E2E Istio release exists in ${ISTIO_NAMESPACE}; remove the partial installation and retry." >&2
    return 1
  fi
  if [[ "$base_exists" == "1" ]]; then
    echo "==> reusing Istio control plane for ${CDS_CLUSTER}"
    return 0
  fi

  if ! kubectl --context "$CDS_CONTEXT" get namespace "$ISTIO_NAMESPACE" \
      >/dev/null 2>&1; then
    ISTIO_NAMESPACE_CREATED=1
  fi
  ISTIO_INSTALL_STARTED=1
  helm repo add istio https://istio-release.storage.googleapis.com/charts --force-update || return 1
  helm repo update istio || return 1
  helm --kube-context "$CDS_CONTEXT" upgrade --install "$ISTIO_BASE_RELEASE" \
    istio/base --namespace "$ISTIO_NAMESPACE" --create-namespace \
    --version "$ISTIO_VERSION" --wait --timeout 5m || return 1
  kubectl --context "$CDS_CONTEXT" wait \
    --for=condition=Established crd/peerauthentications.security.istio.io \
    --timeout=120s || return 1
  helm --kube-context "$CDS_CONTEXT" upgrade --install "$ISTIOD_RELEASE" \
    istio/istiod --namespace "$ISTIO_NAMESPACE" \
    --version "$ISTIO_VERSION" --wait --timeout 5m
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
if ! run_setup "Istio control plane and CRDs available" install_istio; then
  exit 1
fi
if ! run_setup "Helm release installed" "${SCRIPT_DIR}/install.sh" "$E2E_PROFILE"; then
  exit 1
fi

if kubectl --context "$CDS_CONTEXT" -n "$CDS_NAMESPACE" get \
    peerauthentication "${CDS_RELEASE}-mesh-mtls" >/dev/null 2>&1; then
  record_pass "mesh PeerAuthentication is installed"
else
  record_fail "mesh PeerAuthentication was not installed"
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
  "service/${CDS_RELEASE}-dagster-webserver" "${CDS_DAGSTER_FORWARD_PORT}:3000" \
  >"/tmp/${CDS_CLUSTER}-${CDS_RELEASE}-dagster-port-forward.log" 2>&1 &
PORT_FORWARD_PIDS+=("$!")
if wait_for_http "http://127.0.0.1:${CDS_DAGSTER_FORWARD_PORT}/" 'dagster|graphql'; then
  record_pass "Dagster serves HTTP"
else
  record_fail "Dagster HTTP boundary"
fi

kubectl --context "$CDS_CONTEXT" -n "$CDS_NAMESPACE" port-forward \
  "service/${CDS_RELEASE}-superset" "${CDS_SUPERSET_FORWARD_PORT}:8088" \
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
