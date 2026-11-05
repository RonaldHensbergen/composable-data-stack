# Local Postgres + Grafana

Minimal local development stack demonstrating the experimental Grafana
observability module (#369): a Postgres warehouse whose `sql-database`
contract is provisioned as a Grafana "Warehouse" datasource.

## Purpose

Shows how a profile binds the `grafana` module
(`modules-experimental/observability/grafana`, experimental) to a `postgres`
module's `sql-database` contract so Grafana starts with a ready-made Postgres
datasource. Two modules on one bridge network.

## Prerequisites

- Python 3.14+ with the CDS CLI installed, see [docs/installation.md](../../docs/installation.md)
- Docker Engine with the Compose v2 plugin
- The `init-db.sh` script in this directory; it is bind-mounted into postgres and executed on first boot to create the analytics user/database

## Required secrets

Secrets come from environment variables declared in `profile.yaml`. The
aliases and their environment variables are:

| Secret alias | Environment variable |
| --- | --- |
| `postgres_superuser_password` | `CDS_POSTGRES_SUPERUSER_PASSWORD` |
| `analytics_db_password` | `CDS_ANALYTICS_DB_PASSWORD` |
| `grafana_admin_password` | `CDS_GRAFANA_ADMIN_PASSWORD` |

Non-secret config values also come from env: `CDS_ANALYTICS_DB_NAME`,
`CDS_ANALYTICS_DB_USER`. Run `cds init local-postgres-grafana` to generate the
`.env` file from the profile secret definitions (default
`<project-root>/.env`).

## Startup order

- `postgres` starts first, it has no dependencies
- `grafana` depends on `postgres` (it waits for the database healthcheck)

Start the stack with `cds up local-postgres-grafana` (add `-d` to detach).
Grafana is published on the host loopback only, open
`http://localhost:3000` and log in with `admin` / the
`CDS_GRAFANA_ADMIN_PASSWORD` value. The "Warehouse" datasource is
pre-provisioned and points at the analytics database.

## Teardown

- Stop the stack with `docker compose down` in the directory where the compose file was rendered, the default is `<project-root>/docker-compose.yml`
- Add `-v` to also delete the named volumes and rebuild the databases from scratch

## Operational notes

- `metadata.environment` stays `local`
- The `grafana` module is experimental (`modules-experimental/observability/grafana`); it renders for the docker-compose target only and is published on `127.0.0.1` only — front it with the reverse-proxy module for anything beyond local access
- To also chart metrics, add a `metrics-sink` provider advertising `protocol: prometheus` (e.g. a Prometheus module) and set `metricsSink.contractRef` on the `grafana` module; push-style OTLP collectors get no datasource because Grafana cannot query OTLP directly
