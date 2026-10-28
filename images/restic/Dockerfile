# images/restic/Dockerfile
#
# Restic image for the generic file/volume backup module
# (modules-experimental/backup/restic, issue #668):
#   - `cds-restic-scheduler` is the backup sidecar's entrypoint: it takes an
#     initial backup, then a scheduled backup pass of every mounted source
#     with repository retention and integrity checks.
#   - `cds-restic-restore` is the gated one-shot restore service's
#     entrypoint: it restores the newest restore point (or the caller's
#     restic arguments) back into the source volumes/directories.
# Repository location and S3 settings come from the CDS_BACKUP_*
# environment variables rendered by the module; the repository is always
# client-side encrypted with RESTIC_PASSWORD from the profile's secrets.

FROM restic/restic:0.19.1@sha256:136600b6ff6843d61d355f7f71f460a166429f35de6fd11b568fece3c9a4d510

USER root

# Pull in Alpine security fixes for the base image's OS packages before
# creating the user, mirroring images/postgres/Dockerfile: the pinned
# upstream digest can lag behind the fixed packages, and the Trivy scan
# fails on fixable HIGH/CRITICAL OS findings.
RUN apk upgrade --no-cache \
    && adduser -D -u 10000 restic

# Backup repository and source mount points: owned by the restic user so a
# fresh named volume inherits uid/gid 10000 on first use and the sidecar
# (which drops all capabilities and runs as uid 10000) can write without a
# chown step.
RUN mkdir -p /var/lib/restic-repo /backup \
    && chown -R restic:restic /var/lib/restic-repo /backup \
    && chmod 700 /var/lib/restic-repo

COPY --chmod=0555 images/restic/cds-restic-env images/restic/cds-restic-scheduler images/restic/cds-restic-restore /usr/local/bin/

USER restic
