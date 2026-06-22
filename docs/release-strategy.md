# Release Strategy

## Context

Composable Data Stack is in a pre-1.0 phase with a small core team, active documentation work, and a steady stream of correctness and release-engineering improvements. At this stage, the release process should optimize for stability, reviewability, and frequent delivery over broad feature batching.

## Goals For This Phase

- Ship small, well-validated releases on a predictable cadence.
- Keep the main branch green and releasable at all times.
- Prefer correctness, docs, and operational polish over large feature drops.
- Make release decisions from explicit evidence: tests, CI, and changelog entries.

## Recommended Cadence

- Use a weekly release train while the project is still stabilizing.
- Cut a release only when the main branch is green and the changelog is ready.
- If a week does not produce release-worthy changes, skip the tag rather than force a release.

## Branching Model

- Keep feature work in short-lived branches.
- Merge to main only after review and validation.
- Create a release branch when you need to stage a weekly release candidate or include a small set of late-breaking fixes.
- Avoid long-lived release branches unless you are stabilizing a specific milestone.

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

## Phase-Specific Recommendation

For CDS right now, the best strategy is a weekly release train with strict quality gates and narrow scope. That gives contributors a clear rhythm, keeps main trustworthy, and makes it easier to absorb correctness fixes, docs, and CI improvements without slowing the project down.