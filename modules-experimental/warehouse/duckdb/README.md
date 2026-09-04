# DuckDB (experimental)

Warehouse module providing an embedded, file-based
[DuckDB](https://duckdb.org/) database as an alternative to
`modules/warehouse/postgres/` for single-node analytical workloads.
DuckDB is a fast, vectorized/columnar OLAP engine that is widely regarded
as production-grade and competitive with distributed engines like Spark
for single-node analytics -- it is not a "toy" database. The
**experimental** label on this module reflects that the `file-database`
contract shape is new to CDS and not yet exercised by real consumers
(dlt/dbt wiring lands in a follow-up PR), not any immaturity in DuckDB
itself.

## How this differs from postgres

`postgres` is a client-server database: it runs as a long-lived network
service and provides a `sql-database` contract (`host`/`port`/`username`/
`connectionUri`) that any consumer can connect to over the network.

DuckDB is embedded/in-process: it has no server, no network protocol, and
no built-in authentication. The "database" is a single file, opened
directly by whichever process reads or writes it. This module therefore
provides a **`file-database`** contract instead of `sql-database`:

| Field | Description |
| --- | --- |
| `hostDirectory` | Host path holding the DuckDB file. Consumers must bind-mount this exact directory into their own container -- there is no network address to connect to. A relative path resolves consistently against the repository root for every module that references it, so any consumer can reuse the same value as-is. |
| `filename` | File name of the database within `hostDirectory`. |
| `path` | Convenience field combining `hostDirectory` and `filename`. |
| `readOnly` | Whether consumers should treat the file as read-only. |

## What this module does (and doesn't do)

This module does **not** run DuckDB itself -- there is no DuckDB server
process to run. Its `duckdb-init` service is a one-shot job (`restart: no`)
that only prepares the shared `hostDirectory`/`filename` with permissive
file permissions so consumer containers running as different, non-root
users can subsequently create/open the file themselves via the DuckDB
client library embedded in their own process (e.g. Python's `duckdb`
package used by `dlt` and `dbt`'s `dbt-duckdb` adapter).

Consumers wire this contract into their own module by:

1. Declaring a `consumes` entry with `contract.kind: file-database`.
2. Bind-mounting the contract's `hostDirectory` value into their own
   container at whatever path suits them.
3. Opening `<their mount path>/<filename>` with their own DuckDB client
   library -- there is no connection string/driver handshake beyond
   opening the file.

See #593 for the design discussion that led to this contract shape, and
the tracking issue for wiring `dlt`/`dbt` to consume it as their
destination/target.

## Known limitations vs. postgres

- **No BI tool connectivity out of the box.** Superset and other
  `sql-database` consumers expect a network-reachable connection; they
  cannot use this module unless they also bind-mount `hostDirectory` and
  use a DuckDB-specific driver, which most BI tools don't ship by default.
- **No concurrent multi-writer support.** DuckDB allows only one
  read-write connection to a given file at a time; profiles combining
  multiple writers against the same file will need to coordinate access
  (e.g. via `dependsOn` ordering) rather than relying on database-level
  locking guarantees the way postgres provides.
- **Not suitable for multi-node clustering.** DuckDB runs in-process on a
  single node by design (like Spark's single-node/local mode); this module
  targets the same single-node analytical use cases DuckDB itself is built
  for, and complements rather than replaces postgres for workloads that
  need a networked, multi-writer, client-server database.
