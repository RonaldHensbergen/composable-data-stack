# CDS dlt Image

Custom image for the [Composable Data Stack (CDS)](https://github.com/RonaldHensbergen/composable-data-stack)
`modules-experimental/ingestion/dlt` module. Runs a bind-mounted Python
pipeline script with [dlt (data load tool)](https://dlthub.com/) as a
one-shot job, loading into a consumed `sql-database` contract (e.g. the
`postgres` module).

> This image is built and wired automatically by `cds render`/`cds up` for
> profiles that include the `dlt` module — you normally never invoke it
> directly. It is experimental, built locally via the `build:` block in
> `module.yaml`, and (like `images/dagster`/`images/superset`/`images/dbt`)
> also built, scanned, signed, and published to a registry by the repo's
> image workflows.

## Configuration

### Environment variables

| Variable | Required | Purpose |
| --- | --- | --- |
| `DESTINATION__POSTGRES__CREDENTIALS` | yes | Full `postgresql://` connection URI, sourced from the module's consumed `sql-database` contract binding. dlt's postgres destination reads this exact variable name itself. |
| `DLT_PROJECT_DIR` | baked in | Pipeline project directory (default `/usr/app/dlt`), bind-mounted read-only from the host project source |
| `DLT_ENTRYPOINT` | yes | Filename (relative to `DLT_PROJECT_DIR`) that `entrypoint.sh` runs with `python` (default `pipeline.py`) |
| `DLT_PIPELINES_DIR` | baked in | Where dlt persists pipeline working state (schema history, incremental load cursors) across runs (default `/usr/app/dlt_state`, a writable volume — the container filesystem is otherwise read-only) |
| `DLT_DATASET_NAME` / `DLT_PIPELINE_NAME` | no | Passed through for the pipeline script to read via `os.environ` (see `workdirs/dlt/pipeline.py`); dlt itself does not read these automatically |

### Volumes

| Path | Purpose |
| --- | --- |
| `$DLT_PROJECT_DIR` | Pipeline source (the script named by `DLT_ENTRYPOINT`) — mounted **read-only** |
| `$DLT_PIPELINES_DIR` | dlt's local pipeline working directory (schema, state, incremental load cursors) — must persist across runs for incremental sources to work |

## Why no destination beyond Postgres yet?

`images/dlt/requirements.txt` pins `dlt[postgres]`, matching the only
warehouse module CDS currently ships (`modules/warehouse/postgres`). Add the
relevant `dlt[<destination>]` extra and a new `DESTINATION__<NAME>__...` env
var if/when another warehouse module is added.

## Source

- Dockerfile and supporting files: [`images/dlt`](https://github.com/RonaldHensbergen/composable-data-stack/tree/main/images/dlt)
- Module definition: [`modules-experimental/ingestion/dlt/module.yaml`](https://github.com/RonaldHensbergen/composable-data-stack/tree/main/modules-experimental/ingestion/dlt)
- Sample pipeline: [`workdirs/dlt/pipeline.py`](https://github.com/RonaldHensbergen/composable-data-stack/tree/main/workdirs/dlt/pipeline.py)
- Issues and contributions: [RonaldHensbergen/composable-data-stack](https://github.com/RonaldHensbergen/composable-data-stack/issues)
