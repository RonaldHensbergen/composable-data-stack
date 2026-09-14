#!/usr/bin/env bash
# Build the images the profile references with a compose `build:` block.
#
# The Kubernetes manifests set imagePullPolicy: Never for these, because there
# is no registry to pull them from; they reach the cluster through
# `k3d image import` in k3d-up.sh.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/k8s/k3d-env.sh
source "${SCRIPT_DIR}/k3d-env.sh"
cd "$CDS_REPO_ROOT"

echo "==> building local/dagster:custom"
docker build -q -f images/dagster/base/Dockerfile --build-arg DB_BACKEND=postgres -t local/dagster:custom .
echo "==> building local/superset:custom"
# cryptography 46.0.5 is pinned here, not in images/superset/requirements.txt.
# That file requires >=48.0.1, and every version from 48 up dies with SIGILL
# inside the Apple Silicon Docker VM, taking `import superset` with it. The
# repo pin looks security-motivated, so lowering it is a maintainer's call;
# this local override keeps the example profile runnable on arm64 in the
# meantime and rides the Dockerfile's existing IMAGE_PACKAGES hook, which
# installs after the requirements file and therefore wins.
SUPERSET_PACKAGES="${CDS_SUPERSET_PACKAGES:-psycopg2-binary==2.9.12 cryptography==46.0.5}"
docker build -q -f images/superset/base/Dockerfile --build-arg IMAGE_PACKAGES="$SUPERSET_PACKAGES" -t local/superset:custom .
echo "==> done"
docker image ls --format '{{.Repository}}:{{.Tag}}  {{.Size}}' | grep '^local/'
