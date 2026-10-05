# Local Dagster + Postgres + Marquez

Minimal local development stack demonstrating the `lineage-sink` contract:
Dagster wired to a Marquez (OpenLineage) reference backend.

## Purpose

Shows how an orchestrator module consumes the `lineage-sink` contract from
a reference OpenLineage backend (`marquez`, experimental). Three modules
(`postgres`, `marquez`, `dagster`) on one bridge network. See
[docs/observability.md](../../docs/observability.md#data-lineage-the-lineage-sink-contract)
for background on the contract and issue [#779](https://github.com/RonaldHensbergen/composable-data-stack/issues/779).

> **Note:** the upstream `openlineage-dagster` package is currently
> incompatible with the Dagster version pinned by this module (it imports
> a module path removed from modern Dagster releases). CDS wires the
> `OPENLINEAGE_URL`/`OPENLINEAGE_ENDPOINT`/`OPENLINEAGE_API_KEY`/
> `OPENLINEAGE_NAMESPACE` connection environment variables into all three
> Dagster services, but emitting real lineage events still requires
> user-authored job/op code using the `openlineage-python` client
> directly (the smoke test below bypasses Dagster and posts sample
> OpenLineage events straight to Marquez to verify the wiring end to end).

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
| `marquez_db_password` | `CDS_MARQUEZ_DB_PASSWORD` |

Non-secret config values also come from env, `CDS_ANALYTICS_DB_NAME`,
`CDS_ANALYTICS_DB_USER`, `CDS_DAGSTER_DB_NAME`, `CDS_DAGSTER_DB_USER`,
`CDS_MARQUEZ_DB_NAME`, `CDS_MARQUEZ_DB_USER`. Run
`cds init local-dagster-postgres-marquez` to generate the `.env` file from
the profile secret definitions (default `<project-root>/.env`).

## Startup order

- `postgres` starts first, it has no dependencies
- `marquez` depends on `postgres` (its `marquez-api` service runs Flyway migrations on startup)
- `dagster` depends on `postgres` and `marquez`
- inside dagster, the webserver and daemon wait for the `user-code` container healthcheck

Start the stack with `cds up local-dagster-postgres-marquez` (add `-d` to
detach). The web UIs are Dagster on `http://localhost:3000` and the
Marquez web UI on `http://localhost:3001`. The Marquez API is on
`http://localhost:5000`.

## Verifying lineage ingestion

Since `openlineage-dagster` is currently broken upstream (see note above),
verify the `lineage-sink` wiring directly against the Marquez API:

```bash
curl -X POST http://localhost:5000/api/v1/lineage \
  -H "Content-Type: application/json" \
  -d '{
    "eventType": "COMPLETE",
    "eventTime": "2024-01-01T00:00:00.000Z",
    "run": {"runId": "00000000-0000-0000-0000-000000000000"},
    "job": {"namespace": "cds-local", "name": "smoke_test_job"},
    "inputs": [],
    "outputs": [{"namespace": "cds-local", "name": "smoke_test_output_table"}],
    "producer": "https://github.com/RonaldHensbergen/composable-data-stack"
  }'

curl http://localhost:5000/api/v1/namespaces/cds-local/jobs
```

The job and dataset should then be visible in the Marquez web UI.

## Teardown

- Stop the stack with `docker compose down` in the directory where the compose file was rendered, the default is `<project-root>/docker-compose.yml`
- Add `-v` to also delete the named volumes (`postgres-data`, `dagster-io-manager-storage`, `dagster-grpc-socket`) and rebuild the databases from scratch

## Operational notes

- `metadata.environment` stays `local`
- The `marquez` module is experimental (`modules-experimental/observability/marquez`); only `linux/amd64` images are published upstream
- `openlineageNamespace` on the `dagster` module defaults to the profile's choice (`cds-local` here); it is only a convention passed through to user-authored producer code, not auto-read by every OpenLineage client version
