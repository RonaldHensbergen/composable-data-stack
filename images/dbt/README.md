# CDS dbt Image

Custom image for the [Composable Data Stack (CDS)](https://github.com/RonaldHensbergen/composable-data-stack)
`modules-experimental/transformation/dbt` module. Runs `dbt-core` against a
consumed `sql-database` contract (e.g. the `postgres` module) and, optionally,
generates and serves static `dbt docs` output.

> This image is built and wired automatically by `cds render`/`cds up` for
> profiles that include the `dbt` module — you normally never invoke it
> directly. It is **not** currently published to a registry (unlike
> `images/dagster`/`images/superset`); it is experimental and only built
> locally via the `build:` block in `module.yaml`.

## Why Python 3.12, not 3.14?

Every other CDS-owned image (`images/dagster`, `images/superset`) is built on
`python:3.14-slim`. This one deliberately is not: `dbt-core` 1.9.x crashes on
Python 3.14 at import time (`mashumaro.exceptions.UnserializableField: Field
"schema" of type Optional[str] in JSONObjectSchema is not serializable`) due
to a `mashumaro`/typing incompatibility upstream. Verified locally with
`dbt-core==1.9.4` / `dbt-postgres==1.9.0`, both of which install and run
correctly on `python:3.12-slim`. Revisit this pin once dbt Labs ships a
3.14-compatible `dbt-core` release.

## Configuration

### Environment variables

| Variable | Required | Purpose |
| --- | --- | --- |
| `DBT_HOST` / `DBT_PORT` / `DBT_DBNAME` / `DBT_USER` / `DBT_PASSWORD` / `DBT_SCHEMA` | yes | Connection fields for `images/dbt/profiles.yml`'s `cds_target` profile, sourced from the module's consumed `sql-database` contract binding |
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
- Module definition: [`modules-experimental/transformation/dbt/module.yaml`](https://github.com/RonaldHensbergen/composable-data-stack/tree/main/modules-experimental/transformation/dbt)
- Issues and contributions: [RonaldHensbergen/composable-data-stack](https://github.com/RonaldHensbergen/composable-data-stack/issues)
