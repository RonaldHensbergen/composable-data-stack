# images/postgres/Dockerfile
#
# PostgreSQL image with pgBackRest for the built-in backup capability of the
# warehouse/postgres module (issue #665):
#   - `cds-pgbackrest-archive` is used as PostgreSQL's archive_command. It archives
#     WAL segments into the pgBackRest repository when the profile enables
#     the backup capability, and is a no-op (exit 0, segments recycled)
#     otherwise, so the same server configuration runs with and without
#     backups.
#   - `cds-backup-scheduler` is the backup sidecar's entrypoint: it takes an
#     initial full backup, then scheduled full/incremental backups with
#     repository expiry and integrity checks.
# The pgdg apt repository is already configured in the upstream postgres
# image; the pgbackrest package version is pinned for reproducible builds.

FROM postgres:18@sha256:32ca0af8e77bfb8c6610c488e4691f83f972a3e9e64d3b02facf3ab111ad5500

USER root

# Pull in Debian security fixes for the base image's OS packages before
# installing pgbackrest, mirroring images/superset/base/Dockerfile: the
# pinned upstream digest can lag behind trixie-security, and the Trivy
# scan fails on fixable HIGH/CRITICAL OS findings.
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && apt-get install -y --no-install-recommends pgbackrest=2.59.3-1.pgdg13+1 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Backup repository mount point: owned by the postgres user so a fresh named
# volume inherits uid/gid 999 on first use and the sidecar (which drops all
# capabilities and runs as uid 999) can write without a chown step.
RUN mkdir -p /var/lib/pgbackrest \
    && chown postgres:postgres /var/lib/pgbackrest \
    && chmod 700 /var/lib/pgbackrest

COPY --chmod=0555 images/postgres/cds-pgbackrest-archive images/postgres/cds-backup-scheduler images/postgres/cds-restore images/postgres/cds-pgbackrest-get /usr/local/bin/

USER postgres
