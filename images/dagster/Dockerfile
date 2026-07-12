FROM python:3.14-slim AS builder

ENV PYTHONUNBUFFERED=1
WORKDIR /app

COPY images/dagster/requirements.txt ./requirements.txt
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1
ENV DAGSTER_HOME=/opt/dagster/dagster_home

RUN mkdir -p $DAGSTER_HOME && \
    apt-get update && apt-get install -y curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /usr/local/lib/python3.14/site-packages /usr/local/lib/python3.14/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY . /app

COPY images/dagster/workspace.yaml $DAGSTER_HOME
COPY images/dagster/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 3000 4000

HEALTHCHECK --interval=10s --timeout=10s --start-period=30s --retries=3 \
  CMD dagster api grpc-health-check -p 4000 || exit 1

WORKDIR $DAGSTER_HOME

ENTRYPOINT ["/entrypoint.sh"]
# No CMD — let orchestration layer specify it
