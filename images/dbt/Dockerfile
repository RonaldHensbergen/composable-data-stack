FROM python:3.14-slim@sha256:cae66f2ef0ec51a9891263eeee7f987dacf0a9879e8aa9353d5606e0530619a5 AS builder

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

FROM python:3.14-slim@sha256:cae66f2ef0ec51a9891263eeee7f987dacf0a9879e8aa9353d5606e0530619a5

ENV HOME=/home/dbt \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DBT_PROJECT_DIR=/usr/app/dbt \
    DBT_PROFILES_DIR=/home/dbt/.dbt \
    DBT_TARGET_PATH=/usr/app/dbt_target \
    DBT_LOG_PATH=/usr/app/dbt_logs

# Pull in trixie-security fixes (e.g. libssl3t64) not yet baked into the
# pinned python:3.14-slim digest, matching images/superset/base/Dockerfile's
# apt-get upgrade pattern.
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*


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
