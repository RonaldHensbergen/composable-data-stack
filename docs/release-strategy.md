# Release Strategy

## Context

Composable Data Stack is in a pre-1.0 phase with a small core team, active documentation work, and a steady stream of correctness and release-engineering improvements. At this stage, the release process should optimize for stability, reviewability, and frequent delivery over broad feature batching.

## Goals For This Phase

- Ship small, well-validated releases on a predictable cadence.
- Keep the main branch green and releasable at all times.
- Prefer correctness, docs, and operational polish over large feature drops.
- Make release decisions from explicit evidence: tests, CI, and changelog entries.

## Recommended Cadence

- Use a weekly release train while the project is still stabilizing — except while the feature freeze described below is in effect, which supersedes the train with a single manual release-candidate path.
- Cut a release only when the main branch is green and the changelog is ready.
- If a week does not produce release-worthy changes, skip the tag rather than force a release.

## Branching Model

- Keep feature work in short-lived branches.
- Merge to main only after review and validation.
- Create a release branch when you need to stage a weekly release candidate or include a small set of late-breaking fixes.
- Avoid long-lived release branches unless you are stabilizing a specific milestone.

## Branch Policy

- Do not use long-lived `development`, `test`, `acceptance`, or `production` branches for this project phase.
- Use `main` as the single long-lived integration branch.
- Use short-lived topic branches such as `docs/*`, `fix/*`, and `feat/*` for day-to-day work.
- Use short-lived `release/*` branches only when preparing a weekly release candidate.
- Treat tagged releases as the production line instead of maintaining a separate production branch.
- If a hotfix is needed after a tag, use a short-lived `hotfix/*` branch and merge it back through the normal review flow.

## What Belongs In A Weekly Release

- Bug fixes and regressions with clear test coverage.
- Documentation improvements that unblock users or contributors.
- CI, lint, packaging, or release-process improvements.
- Small compatibility fixes that reduce friction across supported environments.

## What Should Wait

- Large refactors without user-facing value.
- Feature work that is not yet covered by tests.
- Changes that alter core behavior without a rollback plan.
- Anything that would require a broad stabilization cycle to verify.

## Release Gates

Before tagging a release, verify:

1. Main is green in CI.
2. The changelog contains the release entry.
3. The release branch has the intended fixes and documentation updates.
4. Unit tests pass locally.
5. Smoke tests pass when the required environment is available.
6. No high-severity blockers remain open for the release window.

## Validation Expectations

At minimum for this phase:

- `python3 -m unittest discover -s tests -p "*.py"`
- `CDS_RUN_DOCKER_SMOKE=1 python3 -m unittest tests.test_compose_runtime_smoke -v` when Docker is available
- `CDS_RUN_DOCKER_SMOKE=1 python3 -m unittest tests.test_compose_profile_lifecycle_smoke -v` when Docker is available (full render -> build -> up -> state -> down lifecycle over the compose profile matrix, #1208)
- Any focused regression tests for the change set being released

Treat a skipped smoke test as an environment limitation, not as release validation.

## Versioning And Changelog

- Use semantic versioning, but expect mostly patch releases until the project is more stable.
- Record every user-facing change in `CHANGELOG.md` before tagging.
- Keep changelog entries concise and grouped by outcome, not by implementation detail.

## Release Checklist

1. Sync main.
2. Confirm the release scope.
3. Update version and changelog.
4. Run the test suite.
5. Cut a release branch if needed.
6. Tag the release.
7. Publish GitHub release notes from the changelog.
8. Announce the release and link any included PRs.

## Rollback Plan

- If a release breaks users, publish a hotfix quickly rather than waiting for the next weekly train.
- Backfill tests for the regression before or alongside the hotfix.
- Document the failure mode in the changelog and release notes.
- For a security vulnerability specifically, follow the severity-based
  delivery targets and emergency release path in
  [`docs/security-support-policy.md`](security-support-policy.md#3-security-update-delivery)
  rather than the general rollback steps above.

## Feature Freeze: v1.0.0-rc1 to v1.0.0

Declared 2026-11-21 in issue #1332 (milestone "v1.0.0 — MVP delivery
(rc1 → final)"). Until `v1.0.0` is tagged, the project is in a feature
freeze:

- **Merge rule.** Only `fix:`, `docs:`, `chore:` (including release/CI
  hardening) and security-fix PRs merge. `feat:`/`enhancement` PRs are
  labelled and parked in the post-1.0.0 backlog (see
  [docs/roadmap.md](roadmap.md)); an exception needs maintainer sign-off
  recorded on #1332.
- **Recorded exception (maintainer sign-off on #1332, 2026-11-21).** PRs
  closing the in-scope issues listed in the 2026-11-19 rescope comment on
  #1332 — #1115, #1136, #1137, #1158–#1162, #1206, #1207, #1242, #1269,
  #1272 — are exempt from the park rule until v1.0.0 final. All other
  `feat:`/`enhancement` PRs stay parked in the post-1.0.0 backlog.
- **Release train suspended.** The weekly train is superseded by one manual
  release-candidate cut: close or supersede the pending weekly bump PR so
  `git-auto-semver` does not fight the manual rc version. After `v1.0.0` the
  weekly train resumes.
- **v1.0.0-rc1 scope.** Cut rc1 against the acceptance checklist in
  [`MVP.md`](../MVP.md) §4 (PR #1331) once that checklist is green: tag
  `v1.0.0-rc1` (PEP 440 `1.0.0rc1`; set `version = "1.0.0rc1"` in
  `pyproject.toml`) and merge to main. rc1 is a **GitHub release only** —
  `pypi.yml` runs `scripts/check_release_version.py --block-prerelease`, so
  pre-releases are never published to production PyPI (the TestPyPI flow in
  [RELEASE.md](../RELEASE.md) stays available to validate the package).
  Announce rc1 with the explicit non-claims: no certified Haven+ compliance;
  the Helm target stays experimental until runbook Gate 4.5
  (#1206/#1207/#1208); the trove classifier stays `3 - Alpha` until G4.7.
- **Final v1.0.0 additionally requires** the runbook Gates 2–5 criteria
  ([docs/plan/k8s-haven-mvp.md](plan/k8s-haven-mvp.md), tracked in #1115):
  the full `production-haven` profile and its live k3d run (#1242, #1269),
  CI-based cluster validation (#76), stability and Beta-readiness
  (#1206/#1207/#1208 → classifier `4 - Beta`), no open high-severity
  blockers, and a complete changelog. #1302 (capability contracts) is
  explicitly cut by this freeze and re-triaged into the post-1.0.0 backlog.

## Phase-Specific Recommendation

For CDS right now, the best strategy is a weekly release train with strict quality gates and narrow scope — except while the feature freeze above is in effect, when the single manual rc1 → 1.0.0 path replaces the train. The train gives contributors a clear rhythm, keeps main trustworthy, and makes it easier to absorb correctness fixes, docs, and CI improvements without slowing the project down.
