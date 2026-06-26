FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app
COPY . /app

COPY requirements.txt .

RUN pip install --upgrade pip && \
    pip install \
        -r requirements.txt && \
        rm requirements.txt