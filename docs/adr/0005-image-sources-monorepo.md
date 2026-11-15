---
type: ADR
id: "0005"
title: "Image sources stay in the monorepo; each image is published as its own registry repository"
status: active
date: 2026-11-15
---

## Context

CDS builds and publishes several image families from `images/` (dagster with
base and hardened variants, dbt, dlt, marquez, postgres, restic, superset).
`.github/workflows/publish-images.yml` pushes each of them to its own
registry repository: `ghcr.io/<owner>/cds-<name>` and
`docker.io/ronaldsoeverein/<name>`, with cosign signatures and Trivy scans.
Module templates reference the images either by digest (`@sha256:...`) or via
the `local/<name>:custom` local-build path, and `image.source: registry` pulls
the published images.

For long-term stability the question was raised whether every produced image
should eventually get its own source repository, so each image could have its
own versioning and push fewer times to Docker Hub.

## Decision

**Image sources stay in this monorepo. The separation between images happens
at the registry level, where it already exists: one repository (package) per
image per registry.** Per-image versioning and reduced push volume are
achieved inside the monorepo (issue #1315): explicit per-image version tags on
both registries, per-image path scoping of the publish workflow, and skipping
pushes when an image's inputs did not change. Splitting the source code into
one git repository per image is rejected.

## Options considered

- **One source repo per image (rejected):** gives each image a fully
  independent release cadence, but duplicates the CI, keyless cosign signing,
  Trivy scanning and `.trivyignore` audits, Renovate, and release automation
  per repo. A base-image security patch would become N pull requests and N
  publish runs, with skew risk between the digest pins in `module.yaml` and
  the images actually published. Cross-image changes lose their atomicity.
- **Monorepo sources, per-image registry repos, per-image version tags
  (chosen):** one PR, one publish run, and one signing pass can update all
  images atomically; each image still has its own registry repository, its own
  digests, signatures, and (with #1315) its own version tags.
- **One shared image repository for all images (rejected):** no per-image
  isolation; a change to one image forces rebuilds and retagging of all, and a
  single version number cannot describe heterogeneous payloads.

## Consequences

- A cross-image change (base image bump, security fix) remains a single PR and
  a single `publish-images.yml` run covering all affected images.
- Versioning is two-layered: the monorepo semver versions the CLI and the
  stack as a whole; per-image version tags (issue #1315) version each
  registry repository independently. Consumers verify by digest, never by
  mutable tag.
- Push volume to Docker Hub and GHCR is controlled by trigger scoping and
  no-op skipping in `publish-images.yml` (issue #1315), not by repository
  layout. Docker Hub's anonymous *pull* rate limits are mitigated by the GHCR
  mirror and digest pinning, not by the number of source repos.
- If an image ever becomes independently consumed outside this stack (for
  example marquez or restic), it can be extracted to its own repository at
  that point; the `module.yaml` references move with it and a new ADR records
  the extraction.
