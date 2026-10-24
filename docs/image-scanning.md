# Image scanning, patching, and remediation SLA

This document describes the continuous vulnerability management process for
the runtime images built in this repository (`dagster`, `superset`), as
tracked by issue #209.

For an exportable summary of module/image evidence for a specific rendered
stack (not this repository-wide scanning process), see
[compliance-report.md](compliance-report.md) (`cds report`).

## Pipeline

| Trigger | Workflow | What happens |
| --- | --- | --- |
| PR touching `images/**` | `image-security-scan.yml` | Builds the images and fails the PR when HIGH/CRITICAL vulnerabilities are found (trivy, `exit-code: 1`, `ignore-unfixed`). |
| Daily (03:23 UTC) | `image-security-scan.yml` | Rescans the **published** digests recorded in `tests/fixtures/signed-images.json`. On HIGH/CRITICAL findings it fails the run and files or updates a `vuln-scan` issue per image with the CVE table and a remediation deadline. |
| Push to `main` touching `images/**` / weekly (Monday 04:17 UTC) | `publish-images.yml` | Rebuilds the images, **fails before push when HIGH/CRITICAL vulnerabilities are found** (trivy gate, `exit-code: 1`, `ignore-unfixed`; same `.trivyignore` exceptions as the PR scan), then signs, attests, and pushes them, and auto-refreshes `tests/fixtures/signed-images.json` via a PR (job `update-fixture`). |
| Base image digest available upstream | Renovate | Opens PRs bumping the digest-pinned base images (`renovate.json`), which are then gated by the PR scan above. |

Because the images re-resolve pip ranges (`>=`) and explicitly upgrade OS
packages at build time, a weekly rebuild picks up available security fixes
even when a pinned base image digest has not changed.

## Versioning

Images under `images/` are versioned and released **independently of the
CLI**. The CLI's own version (`pyproject.toml`, `CHANGELOG.md`, `vX.Y.Z` git
tags) covers `cli/`, `modules/`, and `profiles/` only and says nothing about
which image tags are currently published.

The tag scheme computed by `publish-images.yml`'s "Determine version" step is
the authoritative release record for images — there is no separate GitHub
Release object for them:

- a base version derived from the pinned upstream dependency (`dagster==`
  in `images/dagster/requirements.in`, `FROM apache/superset:` in
  `images/superset/base/Dockerfile`, `dbt-core==` in
  `images/dbt/requirements.in`, `dlt[postgres]==` in
  `images/dlt/requirements.in`),
- an optional `<variant>-` prefix for non-default image variants,
- plus a `sha-<12-char-commit-sha>` tag (immutable, always pushed) and a
  `latest`/`<variant->latest` tag.

To find the currently-published digest for a given image or variant, look up
`tests/fixtures/signed-images.json`: it is refreshed automatically by the
`update-fixture` job in `publish-images.yml` after every successful publish
and records each variant's repository, tag prefix, digest, and
signing/attestation status.

## Hash-locked Python dependencies

Every runtime image that installs Python packages (`dagster`, `dbt`, `dlt`,
and the `superset` overlay) installs from a hash-locked `requirements.txt`
using `--require-hashes`, so an unpinned or hash-mismatched transitive
dependency fails the build instead of silently resolving. Edit the direct
dependencies in the neighboring `requirements*.in` file and regenerate the
lock from that directory (the Python version matches the image's
interpreter: 3.14, or 3.11 for `superset`):

```bash
uv pip compile --generate-hashes --python-version 3.14 \
  -o requirements.txt requirements.in
```

`images/dagster/requirements-postgres.in` includes `requirements.in` and adds
the Postgres storage packages (and the `sqlalchemy<2.1` cap); recompile both.
Renovate maintains these through its `pip-compile` manager, which reads the
`uv pip compile` header in each lock file.

Two supporting locks follow the same rules: `images/superset/requirements-uv.txt`
(the `uv` that performs the hash-verified Superset install) and
`images/dbt/requirements-build.txt` (`packaging`, the only build requirement of
`dbt-core-experimental-parser`). That package publishes only an sdist, a
download-at-install shim whose build backend verifies the wheel it fetches
against a sha256 shipped in the hash-verified sdist. The dbt image installs the
build lock first and then builds with `--no-build-isolation`, so pip never
fetches build requirements unhashed. The `dlt`, Dagster base, and Superset
installs refuse source builds (`--only-binary=:all:` / `--no-build`); the
Alpine-based hardened Dagster image cannot, because some dependencies have no
musllinux wheels. Superset's lock intentionally raises some packages (for CVE
fixes) beyond ranges `apache-superset` declares, so `uv pip check` is not used
as a gate.

The locks are compiled on Linux for the images' `linux/amd64` platform; compile
them on Linux (or in a Linux container) so platform-specific dependencies are
resolved for the right target. CI runs `scripts/check_image_locks.py` to
recompile every lock from its header and fails if `git diff` is non-empty, and
`pip-audit --require-hashes` audits each lock, transitive dependencies
included.

## Remediation SLA

| Severity | Remediation target |
| --- | --- |
| CRITICAL | 72 hours from the `vuln-scan` issue being filed |
| HIGH | 7 days from the `vuln-scan` issue being filed |

The deadline is computed in the scanning workflow and printed in the issue.
Remediation means one of:

1. a Renovate or manual PR that bumps the affected dependency or base
   image digest (merged, then the weekly/triggered rebuild republishes), or
2. an approved exception (below).

Publishing is gated: `publish-images.yml` builds each image and runs the
same HIGH/CRITICAL trivy gate before pushing or signing, so an image with
known findings (e.g. a CVE disclosed after the PR-time scan) is never
republished on `main` or Docker Hub. Exceptions in `.trivyignore` apply to
both gates.

## Exceptions

Exceptions are recorded in `.trivyignore` at the repository root, in
trivy's native ignore format (one ID per line plus an `exp:` expiry):

```text
# patched in the next upstream base image digest
# owner:@alice
CVE-2025-12345 exp:2026-08-01
```

Rules (enforced by `tests/test_trivyignore.py` in CI):

- entries use native trivy syntax: a vulnerability ID followed by an
  `exp:YYYY-MM-DD` expiry.
- `exp` must be a valid date, must not be in the past, and must not be more
  than 90 days out.
- the comment block directly above an entry must name a `owner:@<handle>`
  responsible for the remediation and a justification.
- A vulnerability ID may appear at most once.

An exception expires automatically: after `exp`, the finding is no longer
ignored, the daily scan fails again, and the `vuln-scan` issue is refreshed.
Review `.trivyignore` during the biweekly issue audit
(`biweekly-issue-audit.yml`).

### Resolved exceptions

The `Trivyignore Audit` workflow (`trivyignore-audit.yml`, daily at 03:47
UTC, after the scan) rescans the published images from
`tests/fixtures/signed-images.json` *without* `.trivyignore` and runs
`scripts/audit_trivyignore.py`. It opens or updates a single
`[trivyignore-audit]` issue (label `trivyignore-audit`) listing:

- entries whose ID no longer appears in any published image, which can be
  removed once the next rebuild is confirmed clean, and
- entries still reported although Trivy knows a fixed package version
  (package, installed and fixed version are listed), meaning a base image
  bump or rebuild should pick it up, and
- entries still reported that expire within 14 days.

The audit covers the published digests only, so an entry for an image that
has been rebuilt but not yet published may still be needed; check the PR-time
scan before deleting it.

## Pinned image digest staleness check

The pipeline above only reaches whoever maintains a module's *source*.
A user who has a module's third-party base image pinned to a specific
digest (e.g. `postgres:18@sha256:...` in `modules/warehouse/postgres/module.yaml`)
gets no in-tool signal when the registry republishes that same tag with a
new, patched digest — only out-of-band registry/advisory monitoring
surfaces it.

`cds validate --check-image-digests` and `cds up --check-image-digests`
(also enabled via `CDS_CHECK_IMAGE_DIGESTS=1`) close that gap: for every
digest-pinned image in the resolved profile's modules, CDS queries the
image's registry for the digest currently published for that tag and
emits a `W100` warning when it differs from the pin. The check is:

- **opt-in**, since it makes one network call per distinct pinned image;
  `validate`/`render`/`up` stay network-free without the flag/env var.
- **fail-safe**: an unreachable registry, auth failure, or unsupported
  registry response is skipped silently — it is never reported as an
  error and never fails the command.
- a warning only, not a build gate: a stale pin does not block
  `validate`/`up`, since an older, still-supported digest may be an
  intentional choice.

This surfaces the same "state of the art patching" signal the rest of
this document tracks for CDS's own images, but for a user's *deployed*
pinned digests rather than the images CDS publishes.

## Actions on a `vuln-scan` issue

1. Confirm the finding on the published digest (`Scanned target` in the
   issue body).
2. Remediate per the SLA: open a dependency bump, or request a rebuild if
   the fix is already in the pinned base digest.
3. If the finding cannot be remediated in time, file an exception PR that
   adds a `.trivyignore` entry with owner, justification, and expiry.
4. The daily scan updates the issue body automatically; close the issue
   only after a scan run passes with no findings for that image.
