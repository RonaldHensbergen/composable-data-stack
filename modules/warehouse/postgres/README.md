# PostgreSQL

PostgreSQL database module that provides a sql-database contract.

## Purpose

Provides a PostgreSQL instance for profiles that need relational storage
or a warehouse. One instance can serve several logical databases through
separate contract bindings.

## Known limitations

- The host port is bound to `127.0.0.1` only, so Postgres is not exposed to other hosts
- The init script runs only on first boot when the data volume is empty, later edits to the script or `initDbEnv` do not apply to an existing database
- The container runs read-only with `cap_drop: ALL`, data lives in a named volume and tmpfs

## Upstream documentation

- [PostgreSQL documentation](https://www.postgresql.org/docs/)

## Configuration notes

- `sql-database`, `dagster-database`, and `superset-database` are separate logical databases on the same instance, each with its own user
- `superuserPasswordFrom` sets the `postgres` superuser password via `POSTGRES_PASSWORD`
- `initDbScript` defaults to `init-db.sql` and is mounted into `/docker-entrypoint-initdb.d`, which the official image executes on first boot
- `storage.enabled` toggles the `postgres-data` named volume
