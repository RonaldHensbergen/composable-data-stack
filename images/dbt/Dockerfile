# dbt-core 1.9.x does not run on Python 3.14 yet: `dbt --version`/any command
# crashes at import time with `mashumaro.exceptions.UnserializableField: Field
# "schema" of type Optional[str] in JSONObjectSchema is not serializable`
# (mashumaro's dataclass introspection breaks under 3.14's typing changes).
# Verified locally on 2026-08-05 with dbt-core==1.9.4 / dbt-postgres==1.9.0.
# Pin this image to 3.12-slim instead of the repo's usual 3.14-slim base
# (see images/dagster/Dockerfile, images/superset/Dockerfile) until dbt-core
# ships a 3.14-compatible release; this only affects this module's own
# container image, not the CDS CLI's own Python requirement.
FROM python:3.12-slim@sha256:646fb0bca3dd3ea1bcc6feb72c17ed16eed6e10cffc732fcc1478bd3e7f02d7b AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY images/dbt/requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt

FROM python:3.12-slim@sha256:646fb0bca3dd3ea1bcc6feb72c17ed16eed6e10cffc732fcc1478bd3e7f02d7b

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
    install -d -o dbt -g dbt "$HOME" "$HOME/.dbt" "$DBT_PROJECT_DIR" "$DBT_TARGET_PATH" "$DBT_LOG_PATH"

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY --chmod=0444 images/dbt/profiles.yml /app/images/dbt/profiles.yml
COPY --chmod=0555 images/dbt/entrypoint.sh /entrypoint.sh

USER dbt

WORKDIR $DBT_PROJECT_DIR

ENTRYPOINT ["/entrypoint.sh"]
# No CMD — DBT_COMMANDS (set via the module's compose environment) drives
# what runs; see entrypoint.sh.
