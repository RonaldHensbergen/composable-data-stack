# images/restic

Restic image for the generic file/volume backup module
(`modules/backup/restic`, issue #668).

- Base: `alpine:3.24`, digest-pinned (Renovate keeps the pin current). restic
  itself is compiled from source in a builder stage: `RESTIC_VERSION` pins
  the upstream release tag (0.19.1 is still the latest release) and the
  `go get` line pins the fixed Go module versions from the Trivy advisories
  against the published 0.19.1 binary, compiled with the current Go
  toolchain (#1001); Renovate tracks the ARG and the module pins. The source
  tarball is verified against the pinned `RESTIC_TARBALL_SHA256` during the
  build (bump it together with `RESTIC_VERSION`). Using the prebuilt
  `restic/restic` image instead would reintroduce those findings.
- Adds a dedicated non-root `restic` user (uid 10000) and the repository /
  source mount points (`/var/lib/restic-repo`, `/backup`) with the right
  ownership, so a fresh named volume inherits uid/gid 10000 on first use
  and the sidecar (which drops all capabilities) can write without a chown
  step.
- `cds-restic-env` — shared environment setup sourced by the two
  entrypoints: resolves `RESTIC_REPOSITORY` from `CDS_BACKUP_TARGET_TYPE`
  (the module's own repository volume, or restic's native S3 backend) and
  fails closed when `RESTIC_PASSWORD` is empty.
- `cds-restic-scheduler` — entrypoint of the module's `restic-backup`
  sidecar: idempotent repository creation, a backup pass of every mounted
  source on an interval (one restic snapshot per pass, so the newest
  restore point covers every bound source), retention (`restic forget
  --prune --keep-within`), and `restic check` integrity verification.
- `cds-restic-restore` — entrypoint of the gated one-shot `restic-restore`
  service: stages the newest restore point in `/tmp` and copies each
  restored source into its read-write mount. Extra arguments run restic
  directly.

All repository configuration comes from `RESTIC_*`/`CDS_BACKUP_*`
environment variables rendered by the module (repository location, S3
endpoint/bucket, encryption key from a profile secret, schedule,
retention); no restic config file is used.

Restore and restore-verification procedures are documented in
[docs/backup-restore.md](../../docs/backup-restore.md) and the module
README.

The Trivy scan workflow picks the Dockerfile up automatically
(`images/restic/Dockerfile`). Registry publishing follows the repo-wide
`publish-images.yml` discovery; the signed-images fixture
(`tests/fixtures/signed-images.json`) needs `cds-restic` and
`dockerhub-restic` entries with real digests from the first publish run.
