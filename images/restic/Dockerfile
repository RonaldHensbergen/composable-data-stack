# images/restic/Dockerfile
#
# Restic image for the generic file/volume backup module
# (modules/backup/restic, issue #668):
#   - `cds-restic-scheduler` is the backup sidecar's entrypoint: it takes an
#     initial backup, then a scheduled backup pass of every mounted source
#     with repository retention and integrity checks.
#   - `cds-restic-restore` is the gated one-shot restore service's
#     entrypoint: it restores the newest restore point (or the caller's
#     restic arguments) back into the source volumes/directories.
# Repository location and S3 settings come from the CDS_BACKUP_*
# environment variables rendered by the module; the repository is always
# client-side encrypted with RESTIC_PASSWORD from the profile's secrets.
#
# restic is compiled from source instead of using the upstream
# restic/restic image: 0.19.1 is still the latest upstream release, and its
# published binary is built with vulnerable Go modules and an old stdlib,
# so no upstream image refresh can clear those findings (#1001). Building
# the pinned release tag with the current Go toolchain and the fixed module
# versions below does. The versions are the fixed versions from the Trivy
# advisories (x/net v0.60.0 requires x/crypto >= v0.57.0 and x/text
# >= v0.42.0); Renovate tracks them via the regex manager in renovate.json.

FROM golang:1.27.3-alpine@sha256:d200d2dd76b03a99b52651fd1325aff7dcd35422c35c01bac56571b0507f3deb AS restic-build
ARG RESTIC_VERSION=0.19.1
# sha256 of https://codeload.github.com/restic/restic/tar.gz/refs/tags/v${RESTIC_VERSION}
# (verified stable across downloads). No Renovate datasource exists for
# codeload tarball hashes, so bump this ARG together with RESTIC_VERSION —
# the build fails closed on a mismatch. Module deps fetched by go get/go mod
# tidy are covered by the Go checksum database.
ARG RESTIC_TARBALL_SHA256=bb9b1a19040744d26d8a79be029d4e6b189c45ccc9d8831d7fe367d3c33df725
WORKDIR /src
RUN wget -qO /tmp/restic.tar.gz "https://codeload.github.com/restic/restic/tar.gz/refs/tags/v${RESTIC_VERSION}" \
    && echo "${RESTIC_TARBALL_SHA256}  /tmp/restic.tar.gz" | sha256sum -c - \
    && tar xz --strip-components=1 -f /tmp/restic.tar.gz \
    && rm /tmp/restic.tar.gz \
    && go get github.com/go-jose/go-jose/v4@v4.1.6 \
               github.com/klauspost/compress@v1.18.7 \
               golang.org/x/crypto@v0.57.0 \
               golang.org/x/net@v0.60.0 \
               golang.org/x/text@v0.42.0 \
               google.golang.org/grpc@v1.83.2 \
    && go mod tidy \
    && CGO_ENABLED=0 go build -trimpath -o /restic ./cmd/restic

FROM alpine:3.24@sha256:be2656061b536c1883c4034790e21d0a09c5aaaf51968b89368a3c0371ea2c9a

USER root

COPY --from=restic-build /restic /usr/bin/restic

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
