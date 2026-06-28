FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app
COPY . /app

COPY requirements.txt .

RUN pip install --upgrade pip && \
    pip install \
        -r requirements.txt && \
        rm requirements.txt

ENV DAGSTER_HOME=/opt/dagster/dagster_home/

RUN mkdir -p $DAGSTER_HOME

COPY dagster.yaml workspace.yaml $DAGSTER_HOME

WORKDIR $DAGSTER_HOME
