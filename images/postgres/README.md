# images/postgres

PostgreSQL image with [pgBackRest](https://pgbackrest.org/) for the built-in
backup capability of the `warehouse/postgres` module (issue #665).

- Base: `postgres:18`, digest-pinned (Renovate keeps the pin current).
- Replaces the base image's prebuilt `gosu` binary with a build from the
  pinned gosu tag compiled with the current Go toolchain: the upstream
  image downloads a prebuilt binary that no `postgres:18` tag rebuilds, so
  its Go stdlib CVEs are only fixable by compiling gosu here (#1001).
- Adds the `pgbackrest` package from the pgdg apt repository baked into the
  upstream image, version-pinned for reproducible builds.
- `cds-pgbackrest-archive` — PostgreSQL `archive_command` handler. Archives WAL
  segments into the pgBackRest repository when the profile enables the
  backup capability (`config.backup.enabled: true`), and is a no-op
  otherwise so the same image runs with and without backups.
- `cds-backup-scheduler` — entrypoint of the module's `postgres-backup`
  sidecar: initial full backup, scheduled full/incremental backups,
  repository expiry (retention), and `pgbackrest check` integrity
  verification.

All pgBackRest configuration comes from `PGBACKREST_*` environment variables
rendered by the module (stanza, repository path, cipher key from a profile
secret, retention); no `pgbackrest.conf` is used.

Restore and restore-verification procedures are documented in
[docs/backup-restore.md](../../docs/backup-restore.md) and the module README.

Registry publishing (like the dagster/superset images) is intentionally not
wired up yet; profiles build this image locally via the module's compose
`build` block. The Trivy scan workflow picks the Dockerfile up automatically
(`images/postgres/Dockerfile`).
