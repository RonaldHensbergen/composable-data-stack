FROM python:3.11-slim

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