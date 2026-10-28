# Restic file backup

Generic file/volume backup engine that snapshots data other modules persist
as plain files or directories (rather than through a SQL protocol) into an
encrypted restic repository, on a schedule.

## Purpose

The Postgres warehouse has its own backup capability (pgBackRest,
issue #665), but several modules persist state as files: the orchestration
module's IO-manager volume, a file-based warehouse's host directory, an
ingestion module's pipeline-state volume. This module protects all of them
with one generic engine (issue #668): modules *opt in* by providing a
`backup-source` contract naming the volume or host directory that holds
their durable state, a profile binds those contracts to this module's
source slots (`config.sources.a`/`b`/`c`), and the `restic-backup` sidecar
mounts each bound source read-only and snapshots it every
`config.schedule.intervalMinutes` into the encrypted repository. The module
provides the vendor-neutral `backup-target` contract so production profiles
can pin it with `spec.backup.target.contractRef`.

The default repository target (`config.target.type: file`) is the module's
own `restic-repo` volume -- the zero-dependency local option. Set
`config.target.type: s3` to store the repository on any S3-compatible
endpoint (a cloud bucket, or an in-stack object-storage module once one
lands) via restic's native S3 backend, with credentials resolved through the
profile's existing secrets machinery.

The whole module is a production-only piece: its services render only in
production profiles, and instantiating it elsewhere fails validation
(E143). Restic encrypts the repository client-side and refuses to run
without a key, so `config.encryptionKeyFrom` is a required
`secrets.<alias>` reference (fail closed).

## Restore procedure

The gated one-shot `restic-restore` service restores the newest restore
point into the bound sources:

```sh
docker compose stop <services-writing-the-source>
docker compose --profile cds-restore run --rm restic-restore
docker compose start <services-writing-the-source>
```

Extra arguments run restic directly (e.g. `restic restore <snapshot-id>
--verify-data`). See [docs/backup-restore.md](../../../docs/backup-restore.md)
for the full clean-environment restore-verification procedure, executed
automatically by `tests/test_restic_backup_restore_runtime.py` in the
Docker Runtime Smoke Test workflow.

## Known limitations

- Exactly three source slots (`sources.a`/`b`/`c`) per module instance, each
  bound to one producer's `backup-source` contract. Protecting more data
  sets needs a second restic instance.
- Restored files are owned by the restore service's user
  (`config.restoreUser`, default the image's `restic` user), not by the
  original producer's uid. For bind-kind sources with permissive
  directories this does not matter; for volume-kind sources, set
  `restoreUser` to the uid that owns the volume (e.g. `"999"` for the
  current orchestration and ingestion modules) before restoring, or fix up
  ownership afterwards. busybox `cp` prints a warning about the ownership
  it could not preserve; the data itself is copied with modes and
  timestamps intact.
- The helm render target is not supported; rendering this module's profile
  with `--target=helm` fails with E072 (no Kubernetes workload exists yet).
- The `backup-target` contract's `endpoint`/`repository` fields describe the
  default local repository; when `target.type: s3` is configured, the
  repository is `target.s3.bucket` on `target.s3.endpoint`.
- Sources are mounted read-only into the backup sidecar, which relies on
  producer files being world-readable (the default umask). A producer
  using a restrictive umask would need its contract's data directory
  opened up for reading.

## Upstream documentation

- [restic documentation](https://restic.readthedocs.io/en/stable/)
- [restic S3 backend](https://restic.readthedocs.io/en/stable/030_preparing_a_new_repo.html#on-s3)

## Configuration notes

- `sources.<slot>.contractRef` binds a producer's `backup-source` contract;
  at least one slot must be bound (the config schema fails an all-empty
  `sources` object with E030). A volume-kind source is mounted cross-module
  by its fully rendered Compose volume name; a bind-kind source is the same
  host directory the producer bind-mounts.
- `schedule.intervalMinutes` is the recovery point objective: every pass
  snapshots all bound sources.
- Retention (`retentionDays`) prunes with `restic forget --prune
  --keep-within <days>d` after each pass, and `restic check` verifies
  repository integrity.
