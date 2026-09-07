# dlt (data load tool)

Experimental ingestion module that runs a bind-mounted Python pipeline
script with [dlt](https://dlthub.com/) as a one-shot job, loading data into
a consumed `sql-database` contract (e.g. the `postgres` module).

## Purpose

Fills the ingestion gap in CDS's module set (warehouse, orchestration, bi,
secrets, cache): a provider-neutral way to run extract-load pipelines
against the warehouse without hardcoding which warehouse module is used.
The bundled `workdirs/dlt/pipeline.py` is a placeholder; replace it with a
real source (REST API, files, another database, etc.) when adopting this
module in a profile.

## Known limitations

- Experimental (`productionSuitable: false`): the module runs, but the
  module/config shape may still change before it stabilizes.
- One-shot job only — dlt itself is a library, not a long-running server,
  so there is no health check and `restart: "no"`. Scheduling repeated runs
  (cron, an external trigger, or a future Dagster integration) is outside
  this module's scope; see the non-goals in issue #589.
- Only the `postgres` destination is wired up (`dlt[postgres]` in
  `images/dlt/requirements.txt`), matching the only warehouse module CDS
  currently ships.
- No `provides` contract: since the job produces no long-running service,
  there is nothing for another module to consume yet. A future
  orchestration-triggered "ingestion" contract (so Dagster can consume and
  trigger this pipeline) is tracked as follow-up work in issue #589, not
  implemented here.

## Upstream documentation

- [dlt documentation](https://dlthub.com/docs/intro)
- [dlt postgres destination](https://dlthub.com/docs/dlt-ecosystem/destinations/postgres)

## Configuration notes

- `pipeline.hostPath`/`pipeline.containerPath` bind-mount the pipeline
  project directory read-only, mirroring the `dbt` module's `project.*`
  pattern.
- `entrypointScript` names the file (relative to `pipeline.containerPath`)
  that `images/dlt/entrypoint.sh` runs with `python`.
- `destinationDatabase.contractRef` binds to a `sql-database` contract
  (e.g. `postgres.sql-database`); its `connectionUri` is passed through as
  `DESTINATION__POSTGRES__CREDENTIALS`, the exact environment variable name
  dlt's postgres destination reads itself.
- `destinationDataset`/`pipelineName` are passed through as
  `DLT_DATASET_NAME`/`DLT_PIPELINE_NAME` for the pipeline script to read;
  dlt does not read these automatically, unlike the credentials variable.
- dlt's local pipeline state (schema history, incremental load cursors)
  persists on the `dlt-state` volume across runs.
