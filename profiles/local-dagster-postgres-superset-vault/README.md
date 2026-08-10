# Local Dagster + Postgres + Superset (Vault Secrets)

Minimal local development stack for orchestration, warehouse, and BI.
Includes a Vault dev container for secret bootstrap alongside the stack.

## Purpose

Same stack as `profiles/local-dagster-postgres-superset`, with a `vault`
module added as a sidecar for applications that read secrets from Vault.
The Vault root token is supplied via `CDS_VAULT_TOKEN`. All host ports are
bound to `127.0.0.1` (`5432`, `3000`, `6379`, `8088`, `8200`).

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
| `vault_token` | `CDS_VAULT_TOKEN` |
| `postgres_superuser_password` | `CDS_POSTGRES_SUPERUSER_PASSWORD` |
| `analytics_db_password` | `CDS_ANALYTICS_DB_PASSWORD` |
| `dagster_db_password` | `CDS_DAGSTER_DB_PASSWORD` |
| `superset_db_password` | `CDS_SUPERSET_DB_PASSWORD` |
| `superset_secret_key` | `CDS_SUPERSET_SECRET_KEY` |
| `superset_admin_password` | `CDS_SUPERSET_ADMIN_PASSWORD` |

Non-secret config values also come from env, `CDS_ANALYTICS_DB_NAME`,
`CDS_ANALYTICS_DB_USER`, `CDS_DAGSTER_DB_NAME`, `CDS_DAGSTER_DB_USER`,
`CDS_SUPERSET_DB_NAME`, `CDS_SUPERSET_DB_USER`. Run
`cds init local-dagster-postgres-superset-vault` to generate the `.env` file
from the profile secret definitions (default `<project-root>/.env`).

## Startup order

- `vault`, `postgres`, and `keydb` start first, they have no dependencies
- `dagster` depends on `postgres`, `superset` depends on `postgres` and `keydb`
- inside dagster, the webserver and daemon wait for the `user-code` container healthcheck
- inside superset, the main container starts only after `superset-init` completes

Start the stack with `cds up local-dagster-postgres-superset-vault` (add
`-d` to detach). The web UIs are Dagster on `http://localhost:3000` and
Superset on `http://localhost:8088`.

## Teardown

- Stop the stack with `docker compose down` in the directory where the compose file was rendered, the default is `<project-root>/docker-compose.yml`
- Add `-v` to also delete the named volumes (`postgres-data`, `dagster-io-manager-storage`, `dagster-grpc-socket`) and rebuild the databases from scratch

## Operational notes

- Vault runs in dev mode, its state is ephemeral and is lost on restart, see `modules/secrets/vault/README.md`
- Vault is a sidecar, no other module consumes its `secrets-provider` contract in this profile
- The postgres `storage.size` defaults to `5Gi` and is not overridden, this profile has no `environments/` overlays
