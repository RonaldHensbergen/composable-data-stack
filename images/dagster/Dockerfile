FROM python:3.14-slim@sha256:cea0e6040540fb2b965b6e7fb5ffa00871e632eef63719f0ea54bca189ce14a6 AS builder

ARG DB_BACKEND=postgres

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY images/dagster/requirements*.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt && \
    case "$DB_BACKEND" in \
      postgres) python -m pip install --no-cache-dir -r requirements-postgres.txt ;; \
      sqlite|mysql) ;; \
      *) echo "Unsupported Dagster database backend: $DB_BACKEND" >&2; exit 1 ;; \
    esac

FROM python:3.14-slim@sha256:cea0e6040540fb2b965b6e7fb5ffa00871e632eef63719f0ea54bca189ce14a6

ARG DB_BACKEND=postgres

ENV DAGSTER_HOME=/opt/dagster/dagster_home \
    DAGSTER_IMAGE_DB_BACKEND="$DB_BACKEND" \
    HOME=/opt/dagster/dagster_home \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN groupadd --system --gid 999 dagster && \
    useradd --system --uid 999 --no-create-home --gid dagster dagster && \
    install -d -o dagster -g dagster "$DAGSTER_HOME" /tmp/io_manager_storage /var/run/dagster

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY shared/__init__.py /app/shared/__init__.py
COPY shared/python /app/shared/python
COPY workdirs/dagster /app/workdirs/dagster
COPY images/dagster/generate_config.py images/dagster/healthcheck.py images/dagster/dagster.yaml.j2 /app/images/dagster/
COPY --chmod=0444 images/dagster/workspace.yaml /app/images/dagster/workspace.yaml
COPY --chmod=0555 images/dagster/entrypoint.sh /entrypoint.sh

USER dagster

EXPOSE 3000

HEALTHCHECK --interval=10s --timeout=10s --start-period=30s --retries=3 \
  CMD python /app/images/dagster/healthcheck.py --unix /var/run/dagster/user-code.sock

WORKDIR $DAGSTER_HOME

ENTRYPOINT ["/entrypoint.sh"]
# No CMD — let orchestration layer specify it
