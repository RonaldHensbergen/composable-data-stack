FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1

RUN mkdir -p /opt/dagster/dagster_home
ENV DAGSTER_HOME=/opt/dagster/dagster_home

WORKDIR /app
COPY . /app

COPY requirements.txt .

RUN pip install --upgrade pip && \
    pip install \
        -r requirements.txt && \
        rm requirements.txt

ENV DAGSTER_HOME=/opt/dagster/dagster_home/

RUN apt-get update && apt-get install -y postgresql-client && rm -rf /var/lib/apt/lists/*

COPY dagster.yaml workspace.yaml $DAGSTER_HOME

EXPOSE 3000 4000

HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=3 \
  CMD dagster api grpc-health-check -p 4000 || exit 1

WORKDIR $DAGSTER_HOME

CMD ["dagster", "dev"]
