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

# gosu build stage: the upstream postgres image ships a prebuilt gosu binary
# compiled with an old Go toolchain, and no postgres:18 tag rebuilds it, so
# the Go stdlib CVEs against that binary are not fixable by a base digest
# bump alone (#1001). Build gosu from the pinned upstream tag with the
# current Go toolchain instead; the result is a static binary
# (CGO_ENABLED=0). The source tarball is verified against the pinned
# sha256 below (review hardening on #1041); module dependencies would be
# covered by the Go checksum database, but gosu has none.
FROM golang:1.27.3-alpine@sha256:d200d2dd76b03a99b52651fd1325aff7dcd35422c35c01bac56571b0507f3deb AS gosu
ARG GOSU_VERSION=1.20
# sha256 of https://codeload.github.com/tianon/gosu/tar.gz/refs/tags/${GOSU_VERSION}
# (verified stable across downloads). No Renovate datasource exists for
# codeload tarball hashes, so bump this ARG together with GOSU_VERSION —
# the build fails closed on a mismatch.
ARG GOSU_TARBALL_SHA256=207a2045e890d3ca3326027c5f585733085960b401a49dbc00e3bb628c054cf8
RUN mkdir -p /src \
    && wget -qO /tmp/gosu.tar.gz "https://codeload.github.com/tianon/gosu/tar.gz/refs/tags/${GOSU_VERSION}" \
    && echo "${GOSU_TARBALL_SHA256}  /tmp/gosu.tar.gz" | sha256sum -c - \
    && tar xz --strip-components=1 -C /src -f /tmp/gosu.tar.gz \
    && rm /tmp/gosu.tar.gz \
    && cd /src \
    && CGO_ENABLED=0 go build -trimpath -o /go/bin/gosu .

FROM postgres:18@sha256:979bccfde35445db618c65107d27d08c6c21339d8dc3ad4c830c6633947be0e4

USER root

# Replace the base image's prebuilt gosu with the toolchain-current build
# from the stage above (see the comment on that stage).
COPY --from=gosu /go/bin/gosu /usr/local/bin/gosu

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
