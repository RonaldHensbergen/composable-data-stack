# Object-storage snapshots (experimental)

This module periodically copies the current contents of an
`object-storage` contract to a separately configured S3-compatible
destination. Every successful run writes into a new UTC timestamped prefix.
It uses `rclone copy`, not `sync`, so source deletions and overwrites do not
remove or replace older snapshots.

Garage does not implement S3 object versioning. Timestamped copies provide a
recovery point for earlier object contents without depending on that feature.
The module is experimental and not production-suitable; test restores and
monitor the backup service and destination independently.

Like the other backup modules, it is a production-only piece: its service
renders only in production profiles, and instantiating it in a non-production
profile fails validation (E143). The module is explicitly marked
`productionSuitable: false` because its snapshots are plaintext copies
without automated restore verification, so the security scan raises
CDS-SEC-073 against production profiles that use it; enabling it in
production requires an explicit, scoped runtime-policy waiver for that rule.

## Configuration

| Field | Required | Description |
| --- | --- | --- |
| `sourceStorage.contractRef` | Yes | `object-storage` provider reference, such as `garage.object-storage` |
| `destination.endpoint` | Yes | Destination S3 endpoint URL, including scheme and optional port |
| `destination.bucket` | Yes | Existing destination bucket |
| `destination.region` | Yes | Destination S3 region |
| `destination.accessKeyFrom` | Yes | Profile secret alias with destination write/list access |
| `destination.secretKeyFrom` | Yes | Profile secret alias with destination write/list access |
| `destination.pathStyle` | No | Destination addressing mode; defaults to `true` |
| `prefix` | No | Snapshot prefix inside the destination bucket; defaults to `cds-object-storage-snapshots` |
| `intervalHours` | No | Delay after each successful snapshot; defaults to 24 hours |
| `retryDelaySeconds` | No | Delay before retrying a failed pass; defaults to 300 seconds |

Use separate, least-privilege credentials for the source and destination.
Source credentials should only need to list and read the selected source
bucket. Destination credentials need to list and write objects, but do not
need delete permissions. Prefer a destination on a different backend and host:
another bucket on the source Garage instance does not protect against failure
of that instance or its disks. A destination configured as literally the
bound source endpoint and bucket fails planning with `E147` before anything
boots. Both that check and the service's start-time guard (a backstop for
compose files run without the CLI) are literal string comparisons of the
endpoint and bucket: the same storage reached under a different hostname,
port, or scheme passes them, so the destination choice remains the
operator's responsibility.

## Snapshot layout and recovery

The service starts a snapshot immediately, then sleeps for `intervalHours`
after each successful copy. A failed pass logs the error, leaves its partial
prefix in place, and retries with a fresh timestamp after
`retryDelaySeconds` (300 seconds by default) instead of crash-looping.
Files are stored under:

```text
<prefix>/<source-bucket>/<UTC timestamp>/<object key>
```

For example:

```text
cds-object-storage-snapshots/cds-data/20261028T120000Z/reports/daily.csv
```

Every successful pass ends by writing a `.cds-snapshot-complete` marker
object into its own timestamped prefix. A prefix without that marker is a
partial copy left behind by a failed pass; never restore from it. To restore
a snapshot, copy a marked timestamped prefix back to the source bucket with
an S3 client or `rclone copy`. `rclone copy` overwrites current objects with
the snapshot's older contents without confirmation, so run it with `--dry-run`
first, or restore into a scratch bucket or prefix and verify the contents
before replacing live data. The marker object is copied along with the
snapshot; it is harmless, or exclude it with
`rclone copy --exclude .cds-snapshot-complete`.

The module does not automatically delete
snapshots; configure and review destination lifecycle policies separately so
they retain enough recovery points. Every pass is a full copy of the current
bucket contents into its fresh prefix, so destination storage grows
linearly with the number of retained snapshots -- size the destination and
its lifecycle policy accordingly. It copies object contents, not bucket
policies, access keys, ACLs, or an atomic point-in-time view of a bucket being
actively modified. Validate restores before relying on them: a Docker-gated
runtime proof (`tests/test_object_storage_backup_runtime.py`, run in the
Docker Runtime Smoke Test workflow) exercises exactly this cycle -- seed,
snapshot, overwrite, restore from a completed, marker-verified timestamped
prefix -- against two real Garage instances.

## Example

```yaml
- id: garage
  source: ../../modules/storage/garage
  version: "0.1.0"
  config:
    bucket: cds-data
    accessKeyFrom: secrets.object_storage_access_key
    secretKeyFrom: secrets.object_storage_secret_key
    rpcSecretFrom: secrets.garage_rpc_secret

- id: object-storage-backup
  source: ../../modules-experimental/backup/object-storage
  version: "0.1.0"
  dependsOn:
    - garage
  config:
    sourceStorage:
      contractRef: garage.object-storage
    destination:
      endpoint: https://s3.example.net
      bucket: cds-backups
      region: us-east-1
      accessKeyFrom: secrets.backup_access_key
      secretKeyFrom: secrets.backup_secret_key
    intervalHours: 24
```

Declare all referenced aliases in `spec.secrets.values`. Do not reuse the
source storage credentials for the destination.

## Upstream references

- [rclone S3 backend](https://rclone.org/s3/)
- [rclone copy](https://rclone.org/commands/rclone_copy/)
- [Garage S3 compatibility](https://garagehq.deuxfleurs.fr/documentation/reference-manual/s3-compatibility/)
