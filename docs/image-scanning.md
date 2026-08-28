# Image scanning, patching, and remediation SLA

This document describes the continuous vulnerability management process for
the runtime images built in this repository (`dagster`, `superset`), as
tracked by issue #209.

## Pipeline

| Trigger | Workflow | What happens |
| --- | --- | --- |
| PR touching `images/**` | `image-security-scan.yml` | Builds the images and fails the PR when HIGH/CRITICAL vulnerabilities are found (trivy, `exit-code: 1`, `ignore-unfixed`). |
| Daily (03:23 UTC) | `image-security-scan.yml` | Rescans the **published** digests recorded in `tests/fixtures/signed-images.json`. On HIGH/CRITICAL findings it fails the run and files or updates a `vuln-scan` issue per image with the CVE table and a remediation deadline. |
| Push to `main` touching `images/**` / weekly (Monday 04:17 UTC) | `publish-images.yml` | Rebuilds the images, **fails before push when HIGH/CRITICAL vulnerabilities are found** (trivy gate, `exit-code: 1`, `ignore-unfixed`; same `.trivyignore` exceptions as the PR scan), then signs, attests, and pushes them, and auto-refreshes `tests/fixtures/signed-images.json` via a PR (job `update-fixture`). |
| Base image digest available upstream | Renovate | Opens PRs bumping the digest-pinned base images (`renovate.json`), which are then gated by the PR scan above. |

Because the images re-resolve pip ranges (`>=`) and OS packages at build
time, a weekly rebuild refreshes base layers even when the pinned base
image digest has not changed.

## Versioning

Images under `images/` are versioned and released **independently of the
CLI**. The CLI's own version (`pyproject.toml`, `CHANGELOG.md`, `vX.Y.Z` git
tags) covers `cli/`, `modules/`, and `profiles/` only and says nothing about
which image tags are currently published.

The tag scheme computed by `publish-images.yml`'s "Determine version" step is
the authoritative release record for images — there is no separate GitHub
Release object for them:

- a base version derived from the pinned upstream dependency (`dagster==`
  in `images/dagster/requirements.txt`, `FROM apache/superset:` in
  `images/superset/base/Dockerfile`, `dbt-core==` in
  `images/dbt/requirements.txt`),
- an optional `<variant>-` prefix for non-default image variants,
- plus a `sha-<12-char-commit-sha>` tag (immutable, always pushed) and a
  `latest`/`<variant->latest` tag.

To find the currently-published digest for a given image, look up
`tests/fixtures/signed-images.json`: it is refreshed automatically by the
`update-fixture` job in `publish-images.yml` after every successful publish
and records the repository, digest, and signing/attestation status for each
published image.

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

## Actions on a `vuln-scan` issue

1. Confirm the finding on the published digest (`Scanned target` in the
   issue body).
2. Remediate per the SLA: open a dependency bump, or request a rebuild if
   the fix is already in the pinned base digest.
3. If the finding cannot be remediated in time, file an exception PR that
   adds a `.trivyignore` entry with owner, justification, and expiry.
4. The daily scan updates the issue body automatically; close the issue
   only after a scan run passes with no findings for that image.
