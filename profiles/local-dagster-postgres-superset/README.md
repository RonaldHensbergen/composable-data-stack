# Local Dagster + Postgres + Superset

Minimal local development stack for orchestration, warehouse, and BI.

## Purpose

Runs the reference stack used across the CDS documentation. Four modules
(`postgres`, `dagster`, `keydb`, `superset`) on one bridge network, with all
host ports bound to `127.0.0.1` (`5432`, `3000`, `6379`, `8088`).

## Prerequisites

- Python 3.14+ with the CDS CLI installed, see [docs/installation.md](../../docs/installation.md)
- Docker Engine with the Compose v2 plugin
- The bind-mount sources used by the `dagster` module must exist on the host before `cds up`, `workdirs/dagster/definitions.py` and `workdirs/shared-data`
- The `init-db.sh` script in this directory, it is bind-mounted into postgres and executed on first boot

## Required secrets

Secrets come from environment variables declared in `profile.yaml`. The
aliases and their environment variables are:

| Secret alias | Environment variable |
| --- | --- |
| `postgres_superuser_password` | `CDS_POSTGRES_SUPERUSER_PASSWORD` |
| `analytics_db_password` | `CDS_ANALYTICS_DB_PASSWORD` |
| `dagster_db_password` | `CDS_DAGSTER_DB_PASSWORD` |
| `superset_db_password` | `CDS_SUPERSET_DB_PASSWORD` |
| `superset_secret_key` | `CDS_SUPERSET_SECRET_KEY` |
| `superset_admin_password` | `CDS_SUPERSET_ADMIN_PASSWORD` |

Non-secret config values also come from env, `CDS_ANALYTICS_DB_NAME`,
`CDS_ANALYTICS_DB_USER`, `CDS_DAGSTER_DB_NAME`, `CDS_DAGSTER_DB_USER`,
`CDS_SUPERSET_DB_NAME`, `CDS_SUPERSET_DB_USER`. Run
`cds init local-dagster-postgres-superset` to generate the `.env` file from
the profile secret definitions (default `<project-root>/.env`).

## Startup order

- `postgres` and `keydb` start first, they have no dependencies
- `dagster` depends on `postgres`, `superset` depends on `postgres` and `keydb`
- inside dagster, the webserver and daemon wait for the `user-code` container healthcheck
- inside superset, the main container starts only after `superset-init` completes

Start the stack with `cds up local-dagster-postgres-superset` (add `-d` to
detach). The web UIs are Dagster on `http://localhost:3000` and Superset on
`http://localhost:8088`.

## Teardown

- Stop the stack with `docker compose down` in the directory where the compose file was rendered, the default is `<project-root>/docker-compose.yml`
- Add `-v` to also delete the named volumes (`postgres-data`, `dagster-io-manager-storage`, `dagster-grpc-socket`) and rebuild the databases from scratch

## Operational notes

- The postgres `storage.size` defaults to `5Gi`, the overlays in `environments/` override it (`dev.yaml` `2Gi`, `prod.yaml` `20Gi`)
- `metadata.environment` stays `local` unless an overlay is applied with `--environment`, the prod overlay also enables the production security policy
- The superset healthcheck has a 180 second start period, first boot takes a while before the web UI reports healthy
