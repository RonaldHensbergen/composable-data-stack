ARG PYTHON_IMAGE=python:3.14-slim@sha256:cea0e6040540fb2b965b6e7fb5ffa00871e632eef63719f0ea54bca189ce14a6

FROM ${PYTHON_IMAGE} AS builder

ENV PYTHONUNBUFFERED=1
WORKDIR /app

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY images/dagster/requirements.txt ./requirements.txt
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

FROM ${PYTHON_IMAGE}

ENV PYTHONUNBUFFERED=1
ENV DAGSTER_HOME=/opt/dagster/dagster_home
ENV PATH="/opt/venv/bin:$PATH"

RUN groupadd --system dagster && \
    useradd --system --no-create-home --gid dagster dagster

RUN mkdir -p $DAGSTER_HOME && \
    apt-get update && apt-get install -y curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY . /app

COPY images/dagster/workspace.yaml $DAGSTER_HOME
COPY images/dagster/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

RUN chown -R dagster:dagster \
      "$DAGSTER_HOME" \
      /opt/venv \
      /app

USER dagster

EXPOSE 3000 4000

HEALTHCHECK --interval=10s --timeout=10s --start-period=30s --retries=3 \
  CMD dagster api grpc-health-check -p 4000 || exit 1

WORKDIR $DAGSTER_HOME

ENTRYPOINT ["/entrypoint.sh"]
# No CMD — let orchestration layer specify it
