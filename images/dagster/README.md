# CDS Dagster Image

Dagster orchestration image built for the [Composable Data Stack (CDS)](https://github.com/RonaldHensbergen/composable-data-stack) `orchestration/dagster` module, following the Dagster OSS Docker Compose pattern (webserver + daemon) with pluggable storage backends.

This image is not meant to be run standalone — it is built and wired by CDS profiles via `cds render` / `cds up`, which generate the matching `docker-compose.yml`, `dagster.yaml`, and `workspace.yaml` for your stack.

## Tags

- `latest` — most recent build from `main`
- `<dagster-version>-<yyyymmdd>` — e.g. `1.13.16-20260730`, pinned to the bundled Dagster Python package version and build date

## What's inside

- `dagster`, `dagster-graphql`, `dagster-webserver` (version pinned in `requirements.txt`)
- Optional Postgres run/event storage support (`requirements-postgres.txt`), selected via the `DB_BACKEND` build arg (`postgres` by default; `sqlite` also supported)
- A non-root `dagster` user, `DAGSTER_HOME=/opt/dagster/dagster_home`
- Config templating (`generate_config.py`, `dagster.yaml.j2`) driven by environment variables set by the CDS renderer
- A Unix-socket health check (`healthcheck.py`) for the code server

## Key environment variables

| Variable | Purpose |
| --- | --- |
| `DAGSTER_DB_CONNECTION_URI` | Storage connection string; scheme (`postgresql://`, `sqlite://`) selects the backend at runtime |
| `DAGSTER_IMAGE_DB_BACKEND` | Backend baked into the image at build time (`DB_BACKEND` arg); must match the runtime backend or the container refuses to start |
| `DAGSTER_HOME` | Dagster instance home directory (default `/opt/dagster/dagster_home`) |

## Ports

- `3000` — Dagster webserver (HTTP)

## Source

Dockerfile and supporting files: [`images/dagster`](https://github.com/RonaldHensbergen/composable-data-stack/tree/main/images/dagster)
Module definition: [`modules/orchestration/dagster/module.yaml`](https://github.com/RonaldHensbergen/composable-data-stack/tree/main/modules/orchestration/dagster)
