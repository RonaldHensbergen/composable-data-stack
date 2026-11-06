# Local DuckDB + dlt + dbt

Lightweight local-analytics demo stack: DuckDB as an embedded, file-based
warehouse, loaded by a `dlt` one-shot job and transformed by a `dbt`
one-shot job.

## Purpose

Demonstrates the `file-database` contract end to end: `duckdb` prepares
the shared database file in `workdirs/duckdb/`, `dlt` loads into it
(`destinationType: duckdb` via `duckdb.file-database`), and `dbt` runs
models/tests against the same file (`warehouseType: duckdb` via
`duckdb.file-database`). Three modules (`duckdb`, `dlt`, `dbt`) on one
bridge network; the only host port is `127.0.0.1:8090` for the dbt docs
sidecar. There is no network-facing database service — DuckDB is embedded
in the consumer containers through the shared bind-mounted directory.

## Prerequisites

- Python 3.14+ with the CDS CLI installed, see [docs/installation.md](../../docs/installation.md)
- Docker Engine with the Compose v2 plugin
- The pipeline script bind-mounted from `workdirs/dlt` and the dbt project
  bind-mounted from `workdirs/dbt` must exist on the host (they are
  committed in this repository)

## Required secrets

None — DuckDB is embedded and has no credentials; neither `dlt` in duckdb
mode nor `dbt` with `warehouseType: duckdb` consumes a secret.

## Startup order

- `duckdb-init` runs first: a one-shot job (`restart: "no"`) that creates
  `workdirs/duckdb/warehouse.duckdb` with permissive permissions
- `dlt-run` loads the pipeline into the DuckDB file, then exits
- `dbt-run` runs the configured dbt commands against the same file; the
  `dbt-docs` nginx sidecar only starts after `dbt-run` completes
  successfully

DuckDB allows a single read-write connection per file, so the jobs must
not overlap: keep the `dependsOn` chain (`duckdb` -> `dlt` -> `dbt`) when
editing this profile.

Start the stack with `cds up local-duckdb-dlt-dbt` (add `-d` to detach).
dbt docs are served on `http://localhost:8090` once `dbt-run` completes
with `docs generate` in its command list.

## Teardown

- Stop the stack with `docker compose down` in the directory where the
  compose file was rendered, the default is `<project-root>/docker-compose.yml`
- Add `-v` to also delete the named volumes (`dlt-state`, `dbt-target`,
  `dbt-logs`); delete `workdirs/duckdb/warehouse.duckdb` to rebuild the
  warehouse file from scratch

## Operational notes

- `duckdb-init`, `dlt-run`, and `dbt-run` are one-shot jobs
  (`restart: "no"`), not long-lived services — rerun them with
  `docker compose run <service>` or `cds up` again
- This profile targets single-node, local analytical workloads; for a
  networked, multi-writer database use `local-dagster-postgres-superset*`
  with postgres instead
- `metadata.environment` stays `local` unless an overlay is applied with
  `--environment`
