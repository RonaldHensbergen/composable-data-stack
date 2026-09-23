# Security Support Period And Update Policy

Status: Active policy
Owner: repository maintainer (Ronald Hensbergen)
Related: [`docs/cra-scope-decision.md`](cra-scope-decision.md) (CRA scope
determination), issue
[#731](https://github.com/RonaldHensbergen/composable-data-stack/issues/731)

This is the single authoritative policy for which CDS versions receive
security fixes, how long they receive them, how fixes are delivered, and
where security-relevant history is retained. `SECURITY.md`, `SUPPORT.md`,
`RELEASE.md`, and `docs/release-strategy.md` link here rather than
duplicating these terms.

`docs/support-policy.md` covers a different topic: supported host operating
systems and tooling versions. It does not define a security-fix or
support-period policy; this document does.

## 1. What counts as a supported release

- **A tagged release (`vX.Y.Z`) is the only supported unit.** An untagged
  commit on `main`, including the commit the tag will eventually point to, is
  never itself a supported product release, even though `main` is kept green
  and releasable at all times per
  [`docs/release-strategy.md`](release-strategy.md).
- **Exactly one release line is supported at a time: the most recently
  published tagged release.** CDS does not currently maintain parallel
  long-term-support (LTS) branches for older tags.
- **Pre-1.0 (`0.y.z`) releases** are supported under the same rule above.
  Reaching `1.0.0` does not, by itself, change the single-supported-release
  model described here; a future decision to add LTS branches would be
  recorded as a revision to this document, not assumed in advance.
- When a new release is tagged, the previous release's supported status ends
  as described in [Section 4](#4-end-of-support-communication); it does not
  end at the instant a fix merges into `main`.

## 2. How the support duration was determined

Article 13(8)-(9) of Regulation (EU) 2024/2847 (the Cyber Resilience Act,
"CRA") ties the security-update-availability period to the product's
*expected use*, generally at least five years unless a shorter expected use
is justified, based on: expected use, user expectations, the product's
purpose and environment, comparable products, and core-component lifetimes.
As recorded in
[`docs/cra-scope-decision.md`](cra-scope-decision.md), CDS is currently
assessed as non-commercial free and open-source software under Article
2(12) and therefore **outside the CRA's economic-operator obligations**
today; the statutory minimum in Article 13(8)-(9) is not currently a legal
obligation for this project. This policy is nonetheless written against
that framework so it does not need to be rebuilt from scratch if the
[reassessment triggers](cra-scope-decision.md#mandatory-reassessment-triggers)
in that decision record are ever hit.

Applying the same factors to justify a **short expected-use period** now,
rather than assuming the five-year default:

- **Expected use:** CDS is pre-1.0
  (`Development Status :: 3 - Alpha`, `pyproject.toml`) and changes on a
  weekly release cadence
  ([`docs/release-strategy.md`](release-strategy.md)). Users install it with
  `pip`/`pipx` from PyPI and are expected to track new releases, not pin to
  one indefinitely.
- **User expectations:** the project makes no LTS, backport, or
  multi-version-support commitment anywhere in its documentation; `SECURITY.md`
  historically stated only that the latest code is supported. Users of a
  pre-1.0 alpha CLI do not have a reasonable expectation of multi-year fix
  availability for a specific patch version.
- **Purpose and environment:** CDS renders Docker Compose (and Kubernetes)
  definitions for self-hosted data-platform stacks that operators actively
  build, rebuild, and redeploy; it is not embedded firmware or a
  set-and-forget appliance where re-deployment is impractical.
- **Comparable products:** comparable early-stage, self-hosted developer
  CLIs and compilers commonly support only the latest release during their
  pre-1.0 phase, without multi-year support guarantees.
- **Core-component lifetimes:** CDS depends on Python 3.14+, Docker
  Compose v2+, and upstream module base images
  (see [`docs/os-compatibility.md`](os-compatibility.md)); those upstreams
  are updated far more frequently than a five-year window, and CDS follows
  suit rather than freezing a support commitment against them.

**Determination:** the current justified expected-use and support period is
short — measured in weeks, tracking the weekly release cadence — rather than
years. This determination is recorded here, not assumed by default, and it
must be revisited (see [Section 6](#6-review-and-reassessment)) the moment a
CRA reassessment trigger occurs or the project's release cadence materially
changes (for example, adopting a stable `1.0` LTS branch).

## 3. Security update delivery

| Severity (per `SECURITY.md` triage) | Delivery target |
| --- | --- |
| Critical / High | Emergency (out-of-band) release; see below. Target: fix tagged and released within 7 days of triage, independent of the weekly train. |
| Medium | Included in the next scheduled weekly release. |
| Low | Included in the next scheduled weekly release, or batched if a fix requires larger rework. |

- **Backports:** CDS does not backport fixes to older tags. Because only the
  latest release is supported (see [Section 1](#1-what-counts-as-a-supported-release)),
  the remediation for any severity is always a new release; there is no
  older release line to patch in place.
- **Emergency release path:** an emergency release follows the `hotfix/*`
  branch flow already defined in
  [`docs/release-strategy.md`](release-strategy.md#branch-policy) and the
  rollback plan in [`RELEASE.md`](../RELEASE.md#rollback): branch from the
  affected tag, apply the minimal fix plus a regression test, tag and push
  immediately rather than waiting for the Monday version-bump automation,
  and record the advisory and fix in the same release's `CHANGELOG.md`
  entry and release notes.
- **Discovery:** users discover available updates through GitHub Releases
  (watch the repository or the Releases RSS feed), `CHANGELOG.md`, and
  GitHub Security Advisories filed against this repository. `SUPPORT.md`
  and `SECURITY.md` link to this document for the current mechanism.

## 4. End-of-support communication

Because only the single latest release is supported, a release's support
ends when the next release supersedes it, plus a **14-day upgrade grace
period** during which a reported Critical/High vulnerability in the
just-superseded release still receives an emergency release rather than
being deferred solely to "upgrade to the newer version."

Every GitHub release created by `.github/workflows/release.yml` includes a
generated `## Support` section (see the template in
[`RELEASE.md`](../RELEASE.md#release-notes-template)) stating:

- that the release is supported until superseded, plus the 14-day grace
  period described above, and
- a link to this policy document.

This is generated by `scripts/render_support_notice.py` and verified by
`scripts/check_release_notes_support.py` in CI so a release cannot be
published without a support notice
(see [Section 5](#5-durable-retention)). No release currently has a fixed
calendar end-of-support month/year because the pre-1.0 model is
rolling/superseded-based rather than date-based; if a future LTS branch is
introduced, that branch's release notes will state a fixed end month/year
instead of the rolling model.

## 5. Durable retention

Security-relevant history must not depend on ephemeral CI artifacts (GitHub
Actions artifact retention defaults to 90 days and can be configured shorter).
CDS retains security update and advisory history in locations with no
retention expiry:

- **`CHANGELOG.md`**, committed to git history, is permanent and versioned
  alongside the code it describes.
- **GitHub Releases** (created from tags, which are permanent refs) retain
  the generated release notes, including the `## Support` section.
- **GitHub Security Advisories** filed against this repository are retained
  by GitHub independent of any workflow run and are the private-channel
  record referenced in `SECURITY.md`.

Workflow run logs and any transient CI artifacts are not a retention
mechanism for this information and may be deleted per GitHub's default
retention settings without affecting the record above.

## 6. Review and reassessment

- This document is reviewed at least annually and immediately upon any
  reassessment trigger listed in
  [`docs/cra-scope-decision.md`](cra-scope-decision.md#mandatory-reassessment-triggers),
  since a scope change would make the CRA's statutory support-period
  minimums directly applicable rather than merely informative.
- It is also reviewed if the release cadence materially changes (for
  example, moving off the weekly train, or introducing a `1.0` LTS branch),
  since the "single supported release, superseded-based end of support"
  model in this document assumes the current weekly, single-line cadence.

## References

- Regulation (EU) 2024/2847, Article 13(8), 13(9), 13(19), and Annex II.
- [`docs/cra-scope-decision.md`](cra-scope-decision.md)
- [`docs/release-strategy.md`](release-strategy.md)
- [`RELEASE.md`](../RELEASE.md)
- [`SECURITY.md`](../SECURITY.md)
