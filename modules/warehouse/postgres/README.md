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
- The backup capability (below) is implemented for the compose target only;
  rendering it for the helm target fails closed with `E142`

## Production and learning variants

The module applies the `production` tag convention (see
[docs/modules.md](../../docs/modules.md)):

- Learning/development profiles run the pinned stock `postgres:18` image —
  no local image build, `cds up` only pulls.
- Production profiles gain the production pieces via the `production:`
  mapping: the hardened local build from `images/postgres/` (pgBackRest
  baked in for the backup capability), continuous WAL archiving, and the
  backup repository wiring.

## Backup and disaster recovery (issue #665)

`config.backup` enables the built-in capability (production-only, `E143`
when enabled outside a production profile):

- A `postgres-backup` sidecar takes an initial full backup on start, then
  scheduled full (`fullBackupIntervalHours`, default 24h) and incremental
  (`incrementalIntervalMinutes`, default 60m) backups into the encrypted
  `postgres-backup-repo` volume, and runs `pgbackrest check` integrity
  verification after each backup. Repository retention follows
  `retentionDays` (default 30).
- WAL segments archive continuously (the RPO is the archiving lag, minutes,
  not the backup interval), enabling point-in-time recovery.
- The repository is encrypted client-side (aes-256-cbc) with a key from
  `config.backup.encryptionKeyFrom`; enabling backup without a valid
  `secrets.<alias>` reference fails validation with `E141`.
- The module provides a `backup-target` contract
  (`postgres.backup-target`) for `spec.backup.target.contractRef` in
  production profiles; it advertises `encrypted: true` and
  `supportsRestoreVerification: true` (see
  [docs/backup-restore.md](../../docs/backup-restore.md)).

### Restoring

The gated one-shot `postgres-restore` service restores the latest backup
and replays WAL from the repository:

```sh
docker compose stop postgres
docker compose --profile cds-restore run --rm postgres-restore
docker compose start postgres
```

For restore verification, run the same procedure in a clean environment
(fresh compose project name and volumes, same `CDS_*` secrets) and compare
row counts/checksums against the source; record the restore point age and
elapsed restore time against `rpoMinutes`/`rtoMinutes` as described in
[docs/backup-restore.md](../../docs/backup-restore.md).

## Upstream documentation

- [PostgreSQL documentation](https://www.postgresql.org/docs/)

## Configuration notes

- `sql-database`, `dagster-database`, and `superset-database` are separate logical databases on the same instance, each with its own user
- `superuserPasswordFrom` sets the `postgres` superuser password via `POSTGRES_PASSWORD`
- `initDbScript` defaults to `init-db.sql` and is mounted into `/docker-entrypoint-initdb.d`, which the official image executes on first boot
- `storage.enabled` toggles the `postgres-data` named volume
