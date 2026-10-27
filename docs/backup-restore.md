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
| `E143` | A module's built-in backup capability (a production-only piece) is enabled outside a production profile (#665). |

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
and object-storage
([#669](https://github.com/RonaldHensbergen/composable-data-stack/issues/669))
providers are expected to ship equivalent automated procedures.

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
