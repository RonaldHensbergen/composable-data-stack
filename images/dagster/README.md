# CDS Dagster Image

[![Docker Pulls](https://img.shields.io/docker/pulls/ronaldsoeverein/dagster)](https://hub.docker.com/r/ronaldsoeverein/dagster)
[![Docker Image Size](https://img.shields.io/docker/image-size/ronaldsoeverein/dagster/latest)](https://hub.docker.com/r/ronaldsoeverein/dagster)
[![Docker Image Version](https://img.shields.io/docker/v/ronaldsoeverein/dagster?sort=date)](https://hub.docker.com/r/ronaldsoeverein/dagster/tags)
[![Build Status](https://img.shields.io/github/actions/workflow/status/RonaldHensbergen/composable-data-stack/publish-images.yml?branch=main&label=build)](https://github.com/RonaldHensbergen/composable-data-stack/actions/workflows/publish-images.yml)
[![GitHub Repo](https://img.shields.io/badge/GitHub-source-181717?logo=github)](https://github.com/RonaldHensbergen/composable-data-stack)

Dagster orchestration image built for the [Composable Data Stack (CDS)](https://github.com/RonaldHensbergen/composable-data-stack) `orchestration/dagster` module, following the Dagster OSS Docker Compose pattern (webserver + daemon + code server) with pluggable storage backends.

> This image is normally built and wired automatically by CDS profiles via `cds render` / `cds up`, which generate the matching `docker-compose.yml`, `dagster.yaml`, and `workspace.yaml`. The examples below show how to run it directly if you want to use it outside of CDS.

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
  - [Check the Dagster version](#check-the-dagster-version)
  - [docker-compose with Postgres](#docker-compose-with-postgres)
- [Troubleshooting](#troubleshooting)
- [Source](#source)

## Tags

| Tag | Meaning |
| --- | --- |
| `latest` | Most recent build from `main`; moves as `images/dagster` changes |
| `<dagster-version>-<yyyymmdd>` | Immutable, pinned to the bundled Dagster Python package version and the build date, e.g. `1.13.16-20260730` |

Prefer the pinned `<version>-<yyyymmdd>` tag for anything beyond local experimentation — `latest` will change underneath you.

## Quick Start

```bash
docker pull ronaldsoeverein/dagster:latest
docker run --rm ronaldsoeverein/dagster:latest dagster --version
```

Expected output:

```text
dagster, version 1.13.16
```

## Installation

```bash
# latest
docker pull ronaldsoeverein/dagster:latest

# pinned
docker pull ronaldsoeverein/dagster:1.13.16-20260730
```

## Configuration

### Environment variables

| Variable | Required | Purpose |
| --- | --- | --- |
| `DAGSTER_DB_CONNECTION_URI` | yes | Storage connection string; scheme (`postgresql://`, `sqlite://`) selects the backend at runtime |
| `DAGSTER_IMAGE_DB_BACKEND` | baked in | Backend compiled into the image via the `DB_BACKEND` build arg (`postgres` by default); the container refuses to start if this doesn't match the runtime backend |
| `DAGSTER_HOME` | yes | Dagster instance home directory (default `/opt/dagster/dagster_home`) |

### Volumes

| Path | Purpose |
| --- | --- |
| `$DAGSTER_HOME` | Dagster instance storage (writable, non-root `dagster` user, uid/gid `999`) |
| `/tmp/io_manager_storage` | Default I/O manager storage for local runs |
| `/var/run/dagster` | Unix socket used by `dagster code-server` and consumed by the health check |

### Ports

| Port | Purpose |
| --- | --- |
| `3000` | Dagster webserver (HTTP) |

### Health check

The image ships `healthcheck.py`, used by CDS-rendered Compose services to probe either:

- the code server over its Unix socket: `python /app/images/dagster/healthcheck.py --unix /var/run/dagster/user-code.sock`
- the webserver over HTTP: `python /app/images/dagster/healthcheck.py localhost 3000`

## Examples

### Check the Dagster version

```bash
docker run --rm ronaldsoeverein/dagster:latest dagster --version
```

```text
dagster, version 1.13.16
```

### docker-compose with Postgres

```yaml
services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_USER: dagster
      POSTGRES_PASSWORD: dagster
      POSTGRES_DB: dagster

  dagster-webserver:
    image: ronaldsoeverein/dagster:latest
    depends_on:
      - postgres
    environment:
      DB_BACKEND: postgres
      DAGSTER_DB_CONNECTION_URI: postgresql+psycopg2://dagster:dagster@postgres:5432/dagster
    ports:
      - "3000:3000"
    command: ["dagster-webserver", "-h", "0.0.0.0", "-p", "3000"]
```

## Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `Dagster backend '<x>' does not match image backend '<y>'` | `DAGSTER_DB_CONNECTION_URI` scheme doesn't match the backend the image was built with | Rebuild with the matching `DB_BACKEND` build arg, or point the connection URI at the backend baked into this tag |
| `MySQL storage is not supported by this Dagster image` | MySQL isn't a supported backend for this image | Use `postgres` or `sqlite` |
| Container exits immediately with no logs | No `command` supplied | This image ships with no default `CMD` — pass an explicit command (`dagster-webserver ...`, `dagster code-server start ...`, `dagster-daemon run`) |
| Health check never turns healthy | Unix socket path not shared between the code server and webserver/daemon containers | Mount the same volume at `/var/run/dagster` on every service that needs it |

## Source

- Dockerfile and supporting files: [`images/dagster`](https://github.com/RonaldHensbergen/composable-data-stack/tree/main/images/dagster)
- Module definition: [`modules/orchestration/dagster/module.yaml`](https://github.com/RonaldHensbergen/composable-data-stack/tree/main/modules/orchestration/dagster)
- Issues and contributions: [RonaldHensbergen/composable-data-stack](https://github.com/RonaldHensbergen/composable-data-stack/issues)
