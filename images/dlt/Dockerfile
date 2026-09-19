FROM python:3.14-slim@sha256:0097bb60d0c7a2c6af5a56e747eabc2016218f837f76daa70b9526fb883bc499 AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY images/dlt/requirements.txt ./
# Uninstall pip once dependencies are installed; pip's bundled CycloneDX SBOM
# lists vendored build dependencies (e.g. msgpack, setuptools) at versions
# Trivy flags as vulnerable even though dlt never imports them. Matches
# images/dagster/base/Dockerfile's and images/dbt/Dockerfile's pattern.
RUN python -m pip install --no-cache-dir -r requirements.txt && \
    python -m pip uninstall --yes pip

FROM python:3.14-slim@sha256:0097bb60d0c7a2c6af5a56e747eabc2016218f837f76daa70b9526fb883bc499

ENV HOME=/home/dlt \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Pull in trixie-security fixes not yet baked into the pinned
# python:3.14-slim digest, matching images/superset/base/Dockerfile's and
# images/dbt/Dockerfile's apt-get upgrade pattern.
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system --gid 999 dlt && \
    useradd --system --uid 999 --no-create-home --gid dlt dlt && \
    install -d -o dlt -g dlt "$HOME" "$HOME/.dlt" /usr/app/dlt_state && \
    python -m pip uninstall --yes pip 2>/dev/null || true

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY --chmod=0555 images/dlt/entrypoint.sh /entrypoint.sh

USER dlt

ENTRYPOINT ["/entrypoint.sh"]
# No CMD — DLT_ENTRYPOINT (set via the module's compose environment)
# selects the pipeline script to run; see entrypoint.sh.
