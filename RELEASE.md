# Release Process

This document describes how to create a release.

## Prerequisites

- Main branch is green in CI
- CHANGELOG updated
- No unresolved high-severity blockers
- The release version in `pyproject.toml` has not already been published

## Steps

1. Sync local main:
   ```bash
   git checkout main
   git pull --ff-only
   ```
2. Create a release branch (optional for larger release prep):
   ```bash
   git checkout -b release/vX.Y.Z
   ```
3. Update version in pyproject.toml and changelog.
4. Run tests:
   ```bash
   python -m unittest discover -s tests -p "*.py"
   ```
5. Commit release metadata:
   ```bash
   git add pyproject.toml CHANGELOG.md
   git commit -m "Release vX.Y.Z"
   ```
6. Tag and push:
   ```bash
   git tag vX.Y.Z
   git push origin main --tags
   ```
7. Pushing the tag triggers `.github/workflows/release.yml`, which creates a GitHub release automatically. The release body is populated from the matching `CHANGELOG.md` section (falling back to a note if no entry is found), plus GitHub's auto-generated commit comparison. In practice `action-gh-release` has published every release outright on creation, but the workflow still passes `draft: true` and then verifies the result was actually published, failing the job if a release is ever left as an unpublished draft -- check the workflow run (or the release page) if that happens.
8. If you forget to tag: `.github/workflows/release-tag-reminder.yml` runs on every push to `main` (plus a daily backstop) and opens/updates a tracking issue titled "Release vX.Y.Z is untagged on main" whenever `pyproject.toml`'s version has no matching git tag yet. It auto-closes once the tag exists. Don't rely on it as your only signal -- tag promptly after merging a `chore(release): bump version to X.Y.Z` PR.

## TestPyPI validation

Before enabling production PyPI publishing, validate the exact package flow
through `.github/workflows/testpypi.yml`:

1. Configure the `testpypi` GitHub environment and the matching TestPyPI
   trusted publisher as documented in `docs/packaging.md`.
2. Set a unique PEP 440 version in `pyproject.toml`.
3. Run **Publish CLI to TestPyPI** manually.
4. Install the uploaded wheel using the commands in `docs/packaging.md`.
5. Confirm `cds --help` and security validation work outside a source checkout.

Steps 4-5 are also exercised automatically pre-publish: the shared
`build-python-package.yml` job installs the built wheel and runs a full `cds
get` -> `cds init` -> `cds up` cycle from an empty directory with no
`CDS_PROFILE_PATH`/`CDS_MODULE_PATH` pointing at a source checkout, so any
break in that flow fails CI before publishing rather than only surfacing on
manual post-install testing.

The workflow uses GitHub OIDC (`id-token: write`) and must not be given a PyPI
API token. TestPyPI publication does not publish or reserve the version on
production PyPI.

## Release Notes Template

Use this structure when writing GitHub release notes. Copy relevant sections from `CHANGELOG.md` and remove any that are empty. Write entries in user-facing language. Describe the impact (not the implementation). Credit contributors by GitHub username where applicable (e.g. `— thanks @username`).

### Added

- Brief user-facing description. Reference the PR: (#123)

### Changed

- Brief description of behaviour change. Reference the PR: (#124)

### Fixed

- Brief description of the fix. Reference the closed issue: (#125)

### Breaking

- Description of breaking change and migration steps required.

## Release Checklist

Before publishing the GitHub release:

- [ ] `CHANGELOG.md` updated with all merged PRs since last release
- [ ] Version bumped in `pyproject.toml`
- [ ] Wheel and source distribution pass CI package checks
- [ ] TestPyPI artifact installed and smoke-tested
- [ ] All CI checks green on `main`
- [ ] No unresolved high-severity issues
- [ ] Release notes formatted using the template above
- [ ] Each entry references its PR or issue number
- [ ] Breaking changes are clearly marked and migration steps documented
- [ ] Contributors credited where applicable

## Rollback

If a release is broken:

1. Document issue in release notes
2. Publish hotfix release vX.Y.Z+1
3. Backfill tests for the regression
