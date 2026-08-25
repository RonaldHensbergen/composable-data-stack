FROM python:3.14-slim@sha256:83ff1d245a3d57d04152252d3ef9cb361494d0b3395abd65a5ebe91c401c8e83 AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY images/dbt/requirements.txt ./
# Uninstall pip once dependencies are installed. pip bundles its own
# internal CycloneDX SBOM (pip/_vendor/bom.cdx.json) listing its *vendored*
# build dependencies (e.g. msgpack, setuptools) at versions Trivy flags as
# vulnerable (GHSA-6v7p-g79w-8964, CVE-2025-47273), even though those
# vendored copies are never imported by dbt itself. Removing pip from the
# builder venv drops that SBOM, matching images/dagster/base/Dockerfile's
# pattern; the final stage's own base-image pip is uninstalled separately
# below, since it ships independently of the venv copied in from builder.
RUN python -m pip install --no-cache-dir -r requirements.txt && \
    python -m pip uninstall --yes pip

FROM python:3.14-slim@sha256:83ff1d245a3d57d04152252d3ef9cb361494d0b3395abd65a5ebe91c401c8e83

ENV HOME=/home/dbt \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DBT_PROJECT_DIR=/usr/app/dbt \
    DBT_PROFILES_DIR=/home/dbt/.dbt \
    DBT_TARGET_PATH=/usr/app/dbt_target \
    DBT_LOG_PATH=/usr/app/dbt_logs

RUN groupadd --system --gid 999 dbt && \
    useradd --system --uid 999 --no-create-home --gid dbt dbt && \
    install -d -o dbt -g dbt "$HOME" "$HOME/.dbt" "$DBT_PROJECT_DIR" "$DBT_TARGET_PATH" "$DBT_LOG_PATH" && \
    python -m pip uninstall --yes pip 2>/dev/null || true

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY --chmod=0444 images/dbt/profiles.yml /app/images/dbt/profiles.yml
COPY --chmod=0555 images/dbt/entrypoint.sh /entrypoint.sh

USER dbt

WORKDIR $DBT_PROJECT_DIR

ENTRYPOINT ["/entrypoint.sh"]
# No CMD — DBT_COMMANDS (set via the module's compose environment) drives
# what runs; see entrypoint.sh.
