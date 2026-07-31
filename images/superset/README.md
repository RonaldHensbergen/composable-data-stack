# CDS Superset Image

[![Docker Pulls](https://img.shields.io/docker/pulls/ronaldsoeverein/superset)](https://hub.docker.com/r/ronaldsoeverein/superset)
[![Docker Image Size](https://img.shields.io/docker/image-size/ronaldsoeverein/superset/latest)](https://hub.docker.com/r/ronaldsoeverein/superset)
[![Docker Image Version](https://img.shields.io/docker/v/ronaldsoeverein/superset?sort=date)](https://hub.docker.com/r/ronaldsoeverein/superset/tags)
[![Build Status](https://img.shields.io/github/actions/workflow/status/RonaldHensbergen/composable-data-stack/publish-images.yml?branch=main&label=build)](https://github.com/RonaldHensbergen/composable-data-stack/actions/workflows/publish-images.yml)
[![GitHub Repo](https://img.shields.io/badge/GitHub-source-181717?logo=github)](https://github.com/RonaldHensbergen/composable-data-stack)

Apache Superset image built for the [Composable Data Stack (CDS)](https://github.com/RonaldHensbergen/composable-data-stack) `bi/superset` module.

> This image is normally built and wired automatically by CDS profiles via `cds render` / `cds up`, which generate the matching `docker-compose.yml` and Superset configuration. The examples below show how to run it directly if you want to use it outside of CDS.

## Table of Contents

- [Tags](#tags)
- [Quick Start](#quick-start)
- [Installation](#installation)
- [Configuration](#configuration)
  - [Environment variables](#environment-variables)
  - [Volumes](#volumes)
  - [Ports](#ports)
  - [Health check](#health-check)
- [Examples](#examples)
  - [Check the Superset version](#check-the-superset-version)
  - [docker-compose with Postgres](#docker-compose-with-postgres)
- [Troubleshooting](#troubleshooting)
- [Source](#source)

## Tags

| Tag | Meaning |
| --- | --- |
| `latest` | Most recent build from `main`; moves as `images/superset` changes |
| `<superset-version>-<yyyymmdd>` | Immutable, pinned to the base `apache/superset` version and the build date, e.g. `6.1.0-20260730` |

Prefer the pinned `<version>-<yyyymmdd>` tag for anything beyond local experimentation — `latest` will change underneath you.

## Quick Start

```bash
docker pull ronaldsoeverein/superset:latest
docker run --rm ronaldsoeverein/superset:latest superset version
```

Expected output (banner abbreviated):

```text
...
Version: 6.1.0
```

## Installation

```bash
# latest
docker pull ronaldsoeverein/superset:latest

# pinned
docker pull ronaldsoeverein/superset:6.1.0-20260730
```

## Configuration

### Environment variables

| Variable | Required | Purpose |
| --- | --- | --- |
| `SUPERSET_SECRET_KEY` | yes | Flask/Superset session secret key |
| `SUPERSET__SQLALCHEMY_DATABASE_URI` | yes | Metadata database connection string, triggers `superset db upgrade` on init |
| `SUPERSET__REDIS_URL` | no | Redis connection string used for caching |
| `SUPERSET__CACHE_CONFIG` | no | JSON cache config, e.g. `{"CACHE_TYPE": "redis", "CACHE_REDIS_URL": "...", "CACHE_DEFAULT_TIMEOUT": 300}` |
| `SUPERSET_ADMIN_USERNAME` | no | Admin username created on first init |
| `SUPERSET_ADMIN_PASSWORD` | no | Admin password created on first init |
| `SUPERSET_ADMIN_EMAIL` | no | Admin email created on first init |

### Volumes

| Path | Purpose |
| --- | --- |
| `/app/superset_home` | Superset runtime state (writable, non-root `superset` user) |

### Ports

| Port | Purpose |
| --- | --- |
| `8088` | Superset web UI (HTTP) |

### Health check

Superset exposes a `/health` endpoint once the web process is up; use it as the HTTP health check path in Compose or any orchestrator.

## Examples

### Check the Superset version

```bash
docker run --rm ronaldsoeverein/superset:latest superset version
```

```text
...
Version: 6.1.0
```

### docker-compose with Postgres

```yaml
services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_USER: superset
      POSTGRES_PASSWORD: superset
      POSTGRES_DB: superset

  superset-init:
    image: ronaldsoeverein/superset:latest
    depends_on:
      - postgres
    entrypoint: ["/app/docker/init.sh"]
    environment:
      SUPERSET_SECRET_KEY: "change-me"
      SUPERSET__SQLALCHEMY_DATABASE_URI: postgresql+psycopg2://superset:superset@postgres:5432/superset
      SUPERSET_ADMIN_USERNAME: admin
      SUPERSET_ADMIN_PASSWORD: admin
      SUPERSET_ADMIN_EMAIL: admin@example.com
    restart: "no"

  superset:
    image: ronaldsoeverein/superset:latest
    depends_on:
      superset-init:
        condition: service_completed_successfully
    environment:
      SUPERSET_SECRET_KEY: "change-me"
      SUPERSET__SQLALCHEMY_DATABASE_URI: postgresql+psycopg2://superset:superset@postgres:5432/superset
    ports:
      - "8088:8088"
```

## Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| Web UI never becomes reachable | `superset-init` (migrations/admin setup) hasn't completed yet | Wait for `superset-init` to exit successfully before starting `superset`, as shown above |
| `superset db upgrade` never runs | `SUPERSET__SQLALCHEMY_DATABASE_URI` not set | Migrations only run when this variable is present in the init container's environment |
| Admin login fails | Admin user wasn't created | Ensure `SUPERSET_ADMIN_USERNAME`, `SUPERSET_ADMIN_PASSWORD`, and `SUPERSET_ADMIN_EMAIL` are all set on `superset-init` |
| `pip`/`setuptools` not found in the container | Expected | These are intentionally removed from the image; the app runs from its own `/app/.venv` managed by `uv` |

## Source

- Dockerfile and supporting files: [`images/superset`](https://github.com/RonaldHensbergen/composable-data-stack/tree/main/images/superset)
- Module definition: [`modules/bi/superset/module.yaml`](https://github.com/RonaldHensbergen/composable-data-stack/tree/main/modules/bi/superset)
- Issues and contributions: [RonaldHensbergen/composable-data-stack](https://github.com/RonaldHensbergen/composable-data-stack/issues)
