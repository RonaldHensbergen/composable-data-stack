# cds-runtime: the workload runtime API

`cds-runtime` is a small, installable Python package that gives CDS
workloads one stable API for named database connections, resolved from
CDS contracts and runtime-injected configuration (issue #219). Instead of
every tool duplicating environment-variable knowledge, URI construction,
SQLAlchemy setup, and cleanup in helper files, application code imports
only the runtime API; backend-specific libraries stay internal build-time
dependencies of the workload image.

```python
from cds_runtime import connections

analytics = connections.database("analytics")

rows = analytics.fetch_all(
    "SELECT * FROM customers WHERE active = :active",
    {"active": True},
)

analytics.execute(
    "INSERT INTO audit_log (message) VALUES (:message)",
    {"message": "import completed"},
)
```

## Installation

The package lives in [`runtime/`](../runtime) in this repository:

```bash
pip install "./runtime"             # core, dependency-free
pip install "./runtime[sqlalchemy]" # with SQLAlchemy
pip install "./runtime[postgresql]"  # with SQLAlchemy + psycopg2
pip install "./runtime[mysql]"       # with SQLAlchemy + PyMySQL
```

The core package has no dependencies: SQLAlchemy and the database
drivers are imported lazily, on first use, and a missing dependency is
reported as a typed `DriverUnavailableError` rather than an import-time
crash.

## How connections are resolved

A profile binds each module's `consumes` entry to a provider's contract at
plan time, and the renderer injects the resolved fields into the
workload's environment. `connections.database(name)` reads that
configuration back through a fixed environment-variable convention.

For a connection named `analytics` (hyphens map to underscores, so a
binding named `analytics-database` is just another name):

| Variable | Meaning |
| --- | --- |
| `CDS_DB_ANALYTICS_CONNECTION_URI` | The resolved contract's `connectionUri` — preferred when present |
| `CDS_DB_ANALYTICS_BACKEND` | `postgres` (default), `postgresql`, `mysql`, or `sqlite` |
| `CDS_DB_ANALYTICS_HOST` / `_PORT` / `_DATABASE` / `_USERNAME` / `_PASSWORD` | Fields for postgres/mysql: port must be numeric, 1-65535 (defaults to 5432/3306); password must be set, and an *empty* value selects password-less authentication |
| `CDS_DB_ANALYTICS_NAME` / `_USER` | Legacy spellings (as shipped by the dagster module's environment block), accepted as aliases for `_DATABASE` / `_USERNAME` |
| `CDS_DB_ANALYTICS_SQLITE_PATH` | File path for the `sqlite` backend (`:memory:` supported) |

Field-assembled postgres URLs pin the psycopg2 driver
(`postgresql+psycopg2://`), so they keep working regardless of
SQLAlchemy's default-driver changes (SQLAlchemy >= 2.1 defaults plain
`postgresql://` to psycopg v3). A `CONNECTION_URI` is used verbatim,
including its driver suffix.

Wire it in the workload module's Compose environment, exactly like any
other binding:

```yaml
# modules/apps/my-workload/module.yaml
environment:
  CDS_DB_ANALYTICS_CONNECTION_URI: "${bindings.analytics-database.connectionUri}"
  # or field-by-field:
  # CDS_DB_ANALYTICS_BACKEND: "postgres"
  # CDS_DB_ANALYTICS_HOST: "${bindings.analytics-database.host}"
  # ...
```

`database(name, prefix=...)` overrides the variable prefix, so an
existing environment block in the `CDS_ANALYTICS_DB_*` style (as shipped
by the dagster module, including its legacy `_NAME`/`_USER` field
spellings, which resolve as aliases) can be adopted without touching the
module. Connections resolved from the process environment are cached by
name and prefix: repeated calls share one engine, and two names that
resolve the same environment block stay separate connection objects.

## API

`connections.database(name)` returns a `DatabaseConnection`. Creating it
is side-effect free; the engine is created lazily on the first operation.

| Member | Behavior |
| --- | --- |
| `fetch_all(sql, params=None)` | Query, returning every row as a plain `dict` (provider-neutral) |
| `fetch_one(sql, params=None)` | First row as a `dict`, or `None` |
| `execute(sql, params=None)` | One statement in its own transaction; affected row count (or `-1` when the driver does not report one) |
| `insert_rows(table, rows, *, batch_size=500)` | Bulk insert of homogeneous `dict` rows; identifiers are validated, then quoted through the active dialect (backticks on MySQL, double quotes elsewhere), and values are always bound parameters |
| `transaction()` | Context manager running multiple operations in one explicit transaction: commits on success, rolls back on any exception |
| `close()` | Disposes the cached engine (idempotent); the next operation reconnects |
| `connection_uri` / `redacted_connection_uri` | The resolved URI, and its password-masked form |
| `engine(...)` | The SQLAlchemy engine, as an escape hatch for adapters |

The connection object is a context manager (`with connections.database("analytics") as db:` closes the engine on exit).

Errors are typed and safe to log: `UnknownConnectionError`,
`MissingConnectionSettingsError`, `DriverUnavailableError`,
`ConnectionSetupError`, and `QueryExecutionError`, all subclasses of
`CdsRuntimeError`. Resolution failures name the missing environment
*variables*, never their values; engine-creation failures
(`ConnectionSetupError`) and query failures wrap the underlying error
with the connection's secrets redacted — the `_PASSWORD` field *and* a
password embedded in `CONNECTION_URI`, plus any URI-shaped substring
the driver echoed (values shorter than three characters are left
alone, so a trivially short password does not mangle unrelated words
in the message); and `repr()` of a connection carries no configuration
at all. By design, a wrapped driver error may still carry *data*
values from the failed statement (for example, the conflicting value
in a unique-violation message) — credentials are redacted, data is not.

## Limitations

- Every operation runs inside a transaction, so statements that cannot
  execute inside one (for example PostgreSQL's `VACUUM` or
  `CREATE DATABASE`) fail. Use the `engine()` escape hatch for those.
- The connection registry and lazy engine creation are not synchronized:
  in a multi-threaded workload, two threads may briefly create two
  engines for the same connection. The extra engine is disposed by
  `close()`/`close_all()`; SQLAlchemy engines are themselves thread-safe.
- `QueryExecutionError` and `ConnectionSetupError` carry the driver's
  message (with known secrets redacted), so treat them as safe for logs,
  not as a stable programmatic interface.

## Secrets and rotation

Credentials are supplied through the configured secret provider —
the same `secrets.<alias>` mechanism every module uses — and reach the
workload as Compose runtime placeholders (`${CDS_*}`). cds-runtime reads
them from the environment when the engine is created; it never reads
profile files and never logs values. Rotation therefore follows the
platform's normal path: update the secret, restart the workload (or call
`connections.close_all()` in-process and reconnect).

## Platform metadata vs. workload contracts

Internal platform connections (Superset's metadata database, Dagster's
metadata database) and workload-facing contracts (the analytics
warehouse) are just differently *named* connections — the runtime API
keeps them separate by name, and each resolves its own environment
variables. A workload should only ever be wired to the contracts it
declares in `consumes`.

## Adapters

Optional, framework-specific helpers live in `cds_runtime.adapters`
while the core API stays framework-neutral. Currently:
`cds_runtime.adapters.dagster.postgres_url(name)` resolves a named
connection into the URL Dagster's PostgreSQL storage configuration
expects, and raises `ValueError` when the connection does not resolve to
a `postgresql://` URL (so a mis-bound contract fails up front, not inside
Dagster's storage machinery).

## Migrating from `shared/python/db/connection.py`

The legacy helper is **deprecated** (issue #987): no shipped workdir
loads it anymore. It stays in the images for externally supplied
definitions files that import it; new code should use `cds-runtime`:

| Legacy | cds-runtime |
| --- | --- |
| `get_connection_uri(prefix="ANALYTICS_DB")` | `connections.database("analytics").connection_uri` |
| `get_engine(prefix=...)` | `connections.database("analytics").engine()` |
| `get_connection(prefix=...)` | `connections.database("analytics").engine().connect()` |
| `sql_text(s)` | pass the raw string to `fetch_all`/`execute` |
| `_NAME` / `_USER` fields | accepted natively as aliases for `_DATABASE` / `_USERNAME` |

Adopting an existing `CDS_ANALYTICS_DB_*` environment block does not
require renaming variables: pass `prefix="CDS_ANALYTICS_DB"` — the
legacy `_NAME`/`_USER` field spellings resolve as aliases.

### The shipped workdirs

- The **dagster** workdir resolves its connections through cds-runtime
  (`workdirs/dagster/postgres_connection.py`): the analytics
  connection adopts the module's existing `CDS_ANALYTICS_DB_*` block
  via the prefix, and the dagster-specific event persistence
  (`insert_incoming_file_event`) is ported onto cds-runtime inside the
  workdir — event semantics stay with the workload, connection
  plumbing moved to the package.
- The **example workload** `workdirs/db_roundtrip.py` runs unchanged in
  two CDS-managed Python runtimes: the dagster workdir runs it as the
  `db_roundtrip` asset (canonical `analytics` connection), the dlt
  pipeline runs it as a pre-load check (canonical `destination`
  connection). Both runtime images bake `runtime/cds_runtime` in at
  `/app/runtime`, and the workdir loader falls back to the repo root's
  `runtime/` directory for local runs.
