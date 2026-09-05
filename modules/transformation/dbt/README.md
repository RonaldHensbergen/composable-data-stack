# dbt

dbt-core transformation module that runs a bind-mounted dbt project's
models/tests against a consumed `sql-database` contract as a one-shot job.

## Purpose

Runs `dbt-core` transformations (and optionally `dbt docs generate`) against
any module that provides a `sql-database` contract (e.g. `warehouse/postgres`).
The dbt project itself lives outside the module, bind-mounted read-only from
`config.project.hostPath`, so profiles bring their own models/tests without
forking this module.

## Known limitations

- One-shot job only: the `dbt-run` service exits after running the
  configured `commands` and is not triggered automatically on a schedule —
  pair it with an external scheduler (e.g. a host cron job, or a future
  Dagster-triggered integration) if recurring runs are needed.
- Pinned to Python 3.13 rather than the repo's usual 3.14 base; see
  [`images/dbt/README.md`](../../../images/dbt/README.md) for why.
- The container runs read-only with `cap_drop: ALL`; dbt artifacts/logs live
  in named volumes (`dbt-target`, `dbt-logs`), and `~/.dbt` is a writable
  tmpfs path.
- `dbt docs generate` output is only served if `docs.enabled` is `true`; the
  `dbt-docs` nginx sidecar starts only after `dbt-run` completes
  successfully (`service_completed_successfully`).

## Upstream documentation

- [dbt-core documentation](https://docs.getdbt.com/)
- [dbt-postgres adapter](https://github.com/dbt-labs/dbt-adapters)

## Configuration notes

- `targetDatabase.contractRef` binds the consumed `sql-database` contract
  (e.g. `postgres.sql-database`); connection fields are exposed to the
  container as `DBT_HOST`/`DBT_PORT`/`DBT_DBNAME`/`DBT_USER`/`DBT_PASSWORD`.
- `commands` is a newline-separated list of dbt subcommands run in order by
  the entrypoint (e.g. `run\ntest\ndocs generate`); add `deps` as the first
  line if the project has a `packages.yml`.
- `schema` and `threads` map directly onto dbt's `profiles.yml` target.
- `docs.port` controls the optional `dbt-docs` nginx sidecar's published
  port; set `docs.enabled: false` to skip generating/serving docs entirely.
- KeyDB/Redis is deliberately not wired into this module — dbt-core has no
  built-in use for a Redis-compatible cache.
