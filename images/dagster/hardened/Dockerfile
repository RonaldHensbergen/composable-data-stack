FROM python:3.14-alpine@sha256:c6ead215bfd31f1e433d968853b7a769989117115b728874824e6c0a27cb96fc AS builder

ARG DB_BACKEND=postgres

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app

RUN apk add --no-cache gcc musl-dev libffi-dev

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY images/dagster/requirements*.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt && \
    case "$DB_BACKEND" in \
      postgres) python -m pip install --no-cache-dir -r requirements-postgres.txt ;; \
      sqlite|mysql) ;; \
      *) echo "Unsupported Dagster database backend: $DB_BACKEND" >&2; exit 1 ;; \
    esac && \
    python -m pip uninstall --yes pip

FROM python:3.14-alpine@sha256:c6ead215bfd31f1e433d968853b7a769989117115b728874824e6c0a27cb96fc

ARG DB_BACKEND=postgres

ENV DAGSTER_HOME=/opt/dagster/dagster_home \
    DAGSTER_IMAGE_DB_BACKEND="$DB_BACKEND" \
    HOME=/opt/dagster/dagster_home \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# python:3.14-alpine already reserves gid 999 for a built-in, memberless
# "ping" group (from busybox/alpine-baselayout), so a plain
# `addgroup -g 999 dagster` fails here. Because this RUN used `&&`
# chaining, that failure previously short-circuited the whole line *before*
# reaching `pip uninstall`, silently leaving pip (and its vendored
# msgpack/setuptools copies) in the final image where Trivy flags them as
# HIGH vulnerabilities. Reclaim gid 999 for "dagster" first so the uid/gid
# matches the compose module's tmpfs uid=999,gid=999 mounts, and use `set -e`
# (not `&&`/`|| true`) so any future step failure here fails the build loudly
# instead of silently skipping the pip cleanup again. Uninstalling pip itself
# also removes its vendored msgpack/setuptools copies, so there is no need to
# uninstall those packages separately.
RUN set -e; \
    existing_group="$(getent group 999 | cut -d: -f1 || true)"; \
    if [ -n "$existing_group" ] && [ "$existing_group" != "dagster" ]; then \
      delgroup "$existing_group"; \
    fi; \
    addgroup -g 999 dagster; \
    adduser -D -H -u 999 -G dagster dagster; \
    install -d -o dagster -g dagster "$DAGSTER_HOME" /tmp/io_manager_storage /var/run/dagster; \
    python -m pip uninstall --yes pip

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
