# Backup, Restore, and Disaster Recovery

Version: 0.1
Status: Draft: contract and validation baseline for issue
[#210](https://github.com/RonaldHensbergen/composable-data-stack/issues/210).

## 1. Scope and ownership

This document and the `backup-target` contract own the **vendor-neutral
backup requirements** of a CDS profile: what must be backed up, how long it
is kept, how it is encrypted, and how restores are verified. They do not
implement a backup engine.

| Concern | Owner |
| --- | --- |
| Recovery objectives and threat assumptions | [#212](https://github.com/RonaldHensbergen/composable-data-stack/issues/212) (see [threat-model.md](threat-model.md)) |
| Contract, profile `spec.backup` block, validation (this document) | #210 |
| Postgres-specific backup (pgBackRest/wal-g) | [#665](https://github.com/RonaldHensbergen/composable-data-stack/issues/665) |
| File/volume backup (Dagster IO manager, DuckDB, dlt) | [#668](https://github.com/RonaldHensbergen/composable-data-stack/issues/668) |
| Object-storage bucket backup | [#669](https://github.com/RonaldHensbergen/composable-data-stack/issues/669) |
| Operator procedure verification | [#72](https://github.com/RonaldHensbergen/composable-data-stack/issues/72) ([production-hardening-checklist.md](production-hardening-checklist.md)) |

Module implementations and storage providers stay pluggable: a backup module
*provides* the `backup-target` contract, and the profile binds to it with an
optional `contractRef`.

## 2. The `backup-target` contract

[`shared/contracts/backup-target.yaml`](../shared/contracts/backup-target.yaml)
exposes connection details only: `endpoint`, `repository`, `protocol`,
`encrypted`, and optionally `maxRetentionDays` and
`supportsRestoreVerification`. It never carries key material or storage
credentials; those come from profile secrets (`secrets.<alias>` → `CDS_*`
environment variables resolved by Docker Compose at runtime).

## 3. Profile requirements: `spec.backup`

```yaml
spec:
  secrets:
    values:
      backup_encryption_key:
        env: CDS_BACKUP_ENCRYPTION_KEY
        required: true
  backup:
    target: # optional: pin a backup-target provider
      contractRef: restic.backup-target
    encryption:
      keyFrom: secrets.backup_encryption_key
    policies:
      - workloadClass: warehouse
        retentionDays: 30
        rpoMinutes: 60
        rtoMinutes: 240
        restoreVerification:
          frequencyDays: 30
```

Workload classes:

| Class | Applies when the profile enables a module in category |
| --- | --- |
| `warehouse` | `warehouse` |
| `orchestration-metadata` | `orchestration` |
| `bi-metadata` | `bi` |
| `identity-metadata` | `identity` (e.g. Keycloak's realm/user database) |
| `secrets-metadata` | `secrets` (e.g. Vault's storage backend) |
| `configuration` | always (profile, overlays, module configs; never secret values) |

[`profiles/local-dagster-postgres-superset/environments/prod.yaml`](../profiles/local-dagster-postgres-superset/environments/prod.yaml)
is a complete example.

### Validation rules

Shape and bounds (positive integers, known workload classes, `keyFrom` must
be a `secrets.<alias>` reference rather than inline key material) are
enforced by `profile.schema.json` (`E010`). `cds validate` adds:

| Code | Rule |
| --- | --- |
| `E130` | A workload class has more than one policy. |
| `E131` | `encryption.keyFrom` references an alias missing from `spec.secrets.values`. |
| `E132` | `target.contractRef` is malformed, unknown, not a `backup-target` provider, or (in production) the provider does not advertise `encrypted: true` and `supportsRestoreVerification: true`. |
| `E133` | Production profile has no `spec.backup`, or lacks a policy for a workload class it runs. |
| `E134` | Production profile does not declare backup encryption. |
| `E135` | Production policy has no `restoreVerification`. |
| `E141` | A module's built-in backup capability is enabled without a valid `backup.encryptionKeyFrom` `secrets.<alias>` reference (#665). |
| `E142` | The built-in backup capability is enabled while rendering the `helm` target, which has no backup sidecar workload yet (#665). |
| `E143` | A module's built-in backup capability (a production-only piece) is enabled outside a production profile (#665); also fires for a backup module (`metadata.category: backup`, e.g. restic, #668) instantiated outside a production profile, since its services would silently not render. |
| `E146` | A bound `backup-source` contract advertises a Compose volume the producing module does not declare, so the backup engine would mount an empty implicit volume and protect nothing (plan stage, #668). |

`spec.backup` is optional outside production, but is validated whenever it is
present. Because `cds test` stops later stages on validation errors, a
production profile without compliant backup policies fails before planning.

## 4. Recovery objectives

- **RPO** (`rpoMinutes`): the maximum tolerated window of data loss; the
  backup schedule must run at least this often.
- **RTO** (`rtoMinutes`): the maximum tolerated time from declaring a
  disaster to the workload serving again.
- **Retention** (`retentionDays`): how long restore points are kept. It should not
  exceed the provider's `maxRetentionDays` when that is declared (operator check; not validated yet).

Suggested starting points (tune per workload, derive final values from the threat
model in #212):

| Class | RPO | RTO | Retention |
| --- | --- | --- | --- |
| `warehouse` | 60 min | 240 min | 30 days |
| `orchestration-metadata` | 60 min | 240 min | 30 days |
| `bi-metadata` | 1440 min | 480 min | 30 days |
| `identity-metadata` | 1440 min | 240 min | 30 days |
| `secrets-metadata` | 1440 min | 240 min | 30 days |
| `configuration` | 1440 min | 120 min | 90 days |

## 5. Restore verification procedure

A backup is only trustworthy once restored. For each policy, at least every
`restoreVerification.frequencyDays`:

1. Provision a **clean environment**: a new Compose project name and fresh
   volumes (`cds up` with a distinct `COMPOSE_PROJECT_NAME`), with no
   connectivity to production data stores.
2. Supply the encryption key through the same `CDS_*` secret variable; if the
   key is unavailable the restore must fail, which proves artifacts are
   encrypted.
3. Restore the newest restore point for the workload class using the backup
   module's restore command.
4. Verify integrity: row counts or checksums for warehouse data, Dagster run
   history and Superset dashboards for metadata, and
   `cds validate`/`cds plan` of the restored configuration.
5. Record the measured data-loss window (restore point age) and elapsed
   restore time and compare them with `rpoMinutes` and `rtoMinutes`. Exceeding
   either is a failed verification.
6. Tear down the clean environment.

The profile-level requirements above are unit-tested in
`tests/test_validator_extra.py` (`BackupConfigTest`), and steps 1-6 are
executed end to end for the built-in warehouse provider by
`tests/test_postgres_backup_restore_runtime.py` (Docker-gated, issue
[#210](https://github.com/RonaldHensbergen/composable-data-stack/issues/210)):
it renders the production overlay, takes a full plus incremental backup of
marker data, seeds a clean Compose project with nothing but the encrypted
repository volume, proves a restore with the wrong key fails, restores the
newest restore point, and verifies the data survived byte-for-byte within
the profile's `rpoMinutes`/`rtoMinutes`. Run it locally with
`CDS_RUN_DOCKER_SMOKE=1 python -m unittest
tests.test_postgres_backup_restore_runtime -v`; CI runs it in the Docker
Runtime Smoke Test workflow. The file/volume
([#668](https://github.com/RonaldHensbergen/composable-data-stack/issues/668))
provider ships the equivalent procedure in
`tests/test_restic_backup_restore_runtime.py`: it renders a production
profile binding the restic module to two `backup-source` contracts at once
(a file-based warehouse's bind-kind host directory and the ingestion
module's volume-kind state volume), takes an initial and a second backup
pass — one restic snapshot per pass covering every bound source
([#1014](https://github.com/RonaldHensbergen/composable-data-stack/issues/1014))
— of a marker file in each source, copies only the encrypted repository
volume into a clean compose project with emptied source directories,
proves a restore with the wrong key fails and the markers are absent from
the repository in plaintext, and restores the newest restore point
byte-for-byte into both sources within the profile's
`rpoMinutes`/`rtoMinutes`. The object-storage
([#669](https://github.com/RonaldHensbergen/composable-data-stack/issues/669))
provider is expected to ship an equivalent automated procedure.

## 6. The built-in warehouse backup provider (#665)

The warehouse/postgres module ships the first `backup-target` provider.
Enabling `config.backup` (a production-only piece: enabling it outside a
production profile fails with `E143`) turns on:

- a `postgres-backup` sidecar (pgBackRest) taking an initial full backup
  on start, then scheduled full backups every `fullBackupIntervalHours`
  (default 24) and incremental backups every `incrementalIntervalMinutes`
  (default 60), into the encrypted `postgres-backup-repo` volume;
- continuous WAL archiving into the same repository, so the recovery point
  objective is the WAL archiving lag (minutes), not the backup interval,
  and point-in-time recovery to any archived point is possible;
- client-side repository encryption (aes-256-cbc) with the key from
  `config.backup.encryptionKeyFrom` (required, `E141`);
- `pgbackrest check` repository integrity verification after every
  backup, and retention per `retentionDays` (default 30, advertised as
  `maxRetentionDays`).

Profiles bind it with `spec.backup.target.contractRef:
postgres.backup-target`; the shipped production overlays
(`profiles/local-dagster-postgres-superset/environments/prod.yaml` and
`profiles/team-dagster-postgres-superset/environments/prod.yaml`) are
complete examples.

### Restore procedure

The gated one-shot `postgres-restore` service (compose target only;
`E142` on the helm target) restores the latest backup and replays WAL
from the repository:

```sh
docker compose stop postgres
docker compose --profile cds-restore run --rm postgres-restore
docker compose start postgres
```

For restore verification, provision a clean environment (fresh compose
project name and volumes, same `CDS_*` secret variables), restore there,
and verify the workload data (row counts/checksums) and the measured
restore point age and elapsed time against the profile's
`rpoMinutes`/`rtoMinutes`. Extra arguments to `docker compose run` replace
the service's default restore command wholesale, so repeat the
`--recovery-option` flags when overriding them.

## 7. The generic file backup provider (#668)

The `backup/restic` module (`modules/backup/restic`) is the
generic file/volume backup engine: it protects modules whose durable state
is plain files or directories rather than a SQL database. Where the
Postgres provider (#665) owns both sides (sidecar and repository) inside
one module, the file backup capability is split across two contracts:

- **Opt-in by producers.** A module declares what is worth protecting by
  providing a `backup-source` contract
  ([shared/contracts/backup-source.yaml](../shared/contracts/backup-source.yaml)):
  a *volume* kind exposing the fully rendered Compose volume name (e.g.
  the orchestration module's IO-manager storage volume, the ingestion
  module's pipeline-state volume) or a *bind* kind exposing the host
  directory (e.g. the file-based warehouse's database directory). The
  renderer mounts cross-module volume sources by that exact name without
  re-namespacing them; a contract advertising a volume the producer does
  not declare fails planning with `E146` rather than silently backing up
  an empty implicit volume.
- **Collection by the backup module.** A profile instantiates the restic
  module and binds up to three sources (`config.sources.a`/`b`/`c`) to
  producers' `backup-source` contracts. The `restic-backup` sidecar mounts
  every bound source read-only and runs restic on a schedule
  (`config.schedule.intervalMinutes`): idempotent repository creation, a
  backup pass of all mounted sources (one restic snapshot per pass, so
  the newest restore point covers every bound source), retention
  (`restic forget --prune --keep-within <retentionDays>d`), and
  `restic check` integrity verification. Client-side encryption is
  mandatory (`config.encryptionKeyFrom`, a required `secrets.<alias>`
  reference; restic refuses to run without a key). The module provides the
  `backup-target` contract so production profiles pin it with
  `spec.backup.target.contractRef: <restic-instance>.backup-target`.
- **Pluggable repository.** The default target
  (`config.target.type: file`) is the module's own encrypted
  `restic-repo` volume -- the zero-dependency local option.
  `config.target.type: s3` stores the repository on any S3-compatible
  endpoint via restic's native S3 backend, with credentials resolved
  through the profile's existing secrets machinery, so a real cloud bucket
  and a future in-stack object-storage module need no separate
  integration.

Like the Postgres capability, the whole module is a production-only piece:
its services and repository volume render only in production profiles, and
instantiating it elsewhere fails validation (`E143`).

### Restore procedure

The gated one-shot `restic-restore` service (compose target only; the helm
target fails with `E072` because no Kubernetes workload exists yet)
restores the newest restore point into the bound sources:

```sh
docker compose stop <services-writing-the-source>
docker compose --profile cds-restore run --rm restic-restore
docker compose start <services-writing-the-source>
```

The default command stages the restore in the container's `/tmp` and
copies each restored source into its read-write mount; extra arguments run
restic directly (e.g. `docker compose --profile cds-restore run --rm
restic-restore restore <snapshot-id> --verify-data`). Restored files are
owned by the restore service's user (`config.restoreUser`, default the
image's non-root `restic` user) with modes and timestamps preserved; for
volume-kind sources, set `restoreUser` to the uid owning the volume (e.g.
`"999"` for the current orchestration and ingestion modules) or fix up
ownership afterwards. For restore verification, provision a clean
environment (fresh compose project name, emptied source directories or
fresh source volumes, same `CDS_*` secret variables) and restore there;
`tests/test_restic_backup_restore_runtime.py` executes the full procedure
automatically (see section 5).
