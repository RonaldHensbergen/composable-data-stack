# CDS dbt Image

Custom image for the [Composable Data Stack (CDS)](https://github.com/RonaldHensbergen/composable-data-stack)
`modules/transformation/dbt` module. Runs `dbt-core` against a
consumed target warehouse -- a `sql-database` contract (e.g. the `postgres`
module) or a `file-database` contract (e.g. the `duckdb` module), selected
via `config.warehouseType` -- and, optionally, generates and serves static
`dbt docs` output.

> This image is built and wired automatically by `cds render`/`cds up` for
> profiles that include the `dbt` module — you normally never invoke it
> directly. It is built locally via the `build:` block in `module.yaml`,
> and (like `images/dagster`/`images/superset`) also built, scanned,
> signed, and published to a registry by the repo's image workflows.

## Why Python 3.13, not 3.14?

Every other CDS-owned image (`images/dagster`, `images/superset`) is built on
`python:3.14-slim`. This one deliberately is not: `dbt-core` crashes on
Python 3.14 at import time (`mashumaro.exceptions.UnserializableField: Field
"schema" of type Optional[str] in JSONObjectSchema is not serializable`) due
to a `mashumaro`/typing incompatibility upstream. Verified locally with the
currently pinned `dbt-core==1.11.12` / `dbt-postgres==1.11.0` (see
`images/dbt/requirements.txt`): it crashes on 3.14, but installs and runs
cleanly on `python:3.13-slim` (confirmed with `dbt --version` exiting `0`
with no traceback), which is the smallest possible downgrade from the repo's
usual base. Revisit this pin once dbt Labs ships a 3.14-compatible
`dbt-core` release.

## Configuration

### Environment variables

| Variable | Required | Purpose |
| --- | --- | --- |
| `DBT_WAREHOUSE_TYPE` | yes | Selects the `profiles.yml` target: `postgres` or `duckdb`, sourced from `config.warehouseType`. Determines which of the two variable groups below is required. |
| `DBT_HOST` / `DBT_PORT` / `DBT_DBNAME` / `DBT_USER` / `DBT_PASSWORD` | yes, when `DBT_WAREHOUSE_TYPE=postgres` | Connection fields for the `postgres` output, sourced from the module's consumed `sql-database` contract binding (`config.targetDatabase.contractRef`) |
| `DBT_DUCKDB_PATH` | yes, when `DBT_WAREHOUSE_TYPE=duckdb` | Path to the shared `.duckdb` file for the `duckdb` output: the fixed container path `/usr/app/dbt_duckdb` (hardcoded, not user-configurable) combined with the consumed `file-database` contract's `filename` |
| `DBT_SCHEMA` | yes | Schema/dataset name, shared by both outputs |
| `DBT_THREADS` | no | dbt `threads` setting (default `4`) |
| `DBT_COMMANDS` | yes | Newline-separated list of dbt subcommands to run in order (e.g. `run\ntest\ndocs generate`), executed by `entrypoint.sh` |
| `DBT_PROJECT_DIR` | baked in | dbt project directory (default `/usr/app/dbt`), bind-mounted read-only from the host project source |
| `DBT_PROFILES_DIR` | baked in | Where `profiles.yml` is copied to before each run (default `/home/dbt/.dbt`, a writable tmpfs path — the container filesystem is otherwise read-only) |
| `DBT_TARGET_PATH` / `DBT_LOG_PATH` | baked in | Writable volume paths for dbt artifacts (including generated docs) and logs, kept outside the read-only project mount |

### Volumes

| Path | Purpose |
| --- | --- |
| `$DBT_PROJECT_DIR` | dbt project source (models, `dbt_project.yml`) — mounted **read-only** |
| `$DBT_TARGET_PATH` | dbt artifacts (`manifest.json`, `catalog.json`, generated docs HTML) — shared with the optional `dbt-docs` nginx service |
| `$DBT_LOG_PATH` | dbt run logs |
| `/usr/app/dbt_duckdb` (only when `DBT_WAREHOUSE_TYPE=duckdb`) | the consumed `file-database` contract's shared `hostDirectory`, bind-mounted **read-write** so dbt can create/update tables in the `.duckdb` file |

## Serving docs (optional nginx sidecar)

When the module's `docs.enabled` config is `true`, a second compose service
(`dbt-docs`, built from `nginx:1.27-alpine`) mounts the same `$DBT_TARGET_PATH`
volume read-only and serves it as static files — nothing dbt-specific is
baked into the nginx image itself, so `images/dbt/nginx.conf` is the only
supporting file it needs. Include `docs generate` in `DBT_COMMANDS` for this
to produce anything.

KeyDB/Redis is deliberately **not** wired into this module: dbt-core has no
built-in use for a Redis-compatible cache (unlike Superset's query-result
cache), so adding it here would be an unused dependency rather than a real
integration.

## Source

- Dockerfile and supporting files: [`images/dbt`](https://github.com/RonaldHensbergen/composable-data-stack/tree/main/images/dbt)
- Module definition: [`modules/transformation/dbt/module.yaml`](https://github.com/RonaldHensbergen/composable-data-stack/tree/main/modules/transformation/dbt)
- Issues and contributions: [RonaldHensbergen/composable-data-stack](https://github.com/RonaldHensbergen/composable-data-stack/issues)
