#!/usr/bin/env bash
# Provision the TenderNed dashboard through the local Superset API.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/k8s/k3d-env.sh
source "${SCRIPT_DIR}/../k8s/k3d-env.sh"

if [[ ! -f "${CDS_REPO_ROOT}/.env" ]]; then
  echo "ERROR ${CDS_REPO_ROOT}/.env is required for local Superset credentials." >&2
  exit 1
fi

set -a
# shellcheck disable=SC1091
source "${CDS_REPO_ROOT}/.env"
set +a

export SUPERSET_URL="${SUPERSET_URL:-http://127.0.0.1:${CDS_SUPERSET_PORT}}"
exec "${CDS_REPO_ROOT}/.venv/bin/python" "${SCRIPT_DIR}/provision-dashboard.py"
