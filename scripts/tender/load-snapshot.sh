#!/usr/bin/env bash
# Copy a local TenderNed snapshot into the Dagster volume and materialize it.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/k8s/k3d-env.sh
source "${SCRIPT_DIR}/../k8s/k3d-env.sh"

SNAPSHOT="${1:-${CDS_REPO_ROOT}/data/tenderned/tenderned-tenders.csv.gz}"
CONTAINER_SNAPSHOT="${CDS_TENDER_SNAPSHOT_CONTAINER_PATH:-/app/data/cds/incoming/tenderned-tenders.csv.gz}"
WAIT_SECONDS="${CDS_TENDER_LOAD_TIMEOUT:-600}"

if [[ ! -f "$SNAPSHOT" ]]; then
  echo "ERROR TenderNed snapshot not found: ${SNAPSHOT}" >&2
  echo "      Run scripts/tender/export-snapshot.sh first." >&2
  exit 1
fi

USER_CODE_POD="$(kubectl --context "$CDS_CONTEXT" --namespace "$CDS_NAMESPACE" \
  get pods \
  -l "app.kubernetes.io/instance=${CDS_RELEASE},app.kubernetes.io/name=dagster-user-code" \
  -o jsonpath='{.items[0].metadata.name}')"
if [[ -z "$USER_CODE_POD" ]]; then
  echo "ERROR Dagster user-code pod was not found in ${CDS_NAMESPACE}." >&2
  exit 1
fi

echo "==> copying snapshot into ${USER_CODE_POD}"
kubectl --context "$CDS_CONTEXT" --namespace "$CDS_NAMESPACE" \
  exec -i "$USER_CODE_POD" -- sh -ceu '
    target="$1"
    mkdir -p "$(dirname "$target")"
    temporary="${target}.tmp.$$"
    trap '\''rm -f "$temporary"'\'' EXIT
    cat > "$temporary"
    chmod 0600 "$temporary"
    mv "$temporary" "$target"
    trap - EXIT
  ' sh "$CONTAINER_SNAPSHOT" < "$SNAPSHOT"

LOCAL_SHA="$(shasum -a 256 "$SNAPSHOT" | awk '{print $1}')"
REMOTE_SHA="$(kubectl --context "$CDS_CONTEXT" --namespace "$CDS_NAMESPACE" \
  exec "$USER_CODE_POD" -- sha256sum "$CONTAINER_SNAPSHOT" | awk '{print $1}')"
if [[ "$LOCAL_SHA" != "$REMOTE_SHA" ]]; then
  echo "ERROR Snapshot checksum changed during transfer." >&2
  exit 1
fi

if command -v gtimeout >/dev/null 2>&1; then
  TIMEOUT_BIN="gtimeout"
elif command -v timeout >/dev/null 2>&1; then
  TIMEOUT_BIN="timeout"
else
  echo "ERROR A timeout command is required (gtimeout or timeout)." >&2
  exit 1
fi

echo "==> materializing load_tender_analytics_job through Dagster"
"$TIMEOUT_BIN" "${WAIT_SECONDS}s" \
  kubectl --context "$CDS_CONTEXT" --namespace "$CDS_NAMESPACE" \
  exec "$USER_CODE_POD" -- \
  dagster job execute \
    --python-file /app/workdirs/dagster/definitions.py \
    --job load_tender_analytics_job

POSTGRES_POD="${CDS_RELEASE}-postgres-0"
echo "==> verifying local analytical model"
kubectl --context "$CDS_CONTEXT" --namespace "$CDS_NAMESPACE" \
  exec "$POSTGRES_POD" -- sh -ceu '
    export PGPASSWORD="$ANALYTICS_DB_PASSWORD"
    psql -X -v ON_ERROR_STOP=1 -U "$ANALYTICS_DB_USER" -d "$ANALYTICS_DB_NAME" \
      -P pager=off -c "
        SELECT
          COUNT(*) AS tenders,
          MIN(publicatie_datum) AS earliest,
          MAX(publicatie_datum) AS latest,
          COUNT(*) FILTER (WHERE has_pdf) AS pdf_enriched
        FROM tender_analytics.tenders;
      "
  '
