# Release Process

This document describes how to create a release.

## Prerequisites

- Main branch is green in CI
- CHANGELOG updated
- No unresolved high-severity blockers

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
7. Pushing the tag triggers `.github/workflows/release.yml`, which creates a draft GitHub release automatically. The release body is populated from the matching `CHANGELOG.md` section (falling back to a note if no entry is found), plus GitHub's auto-generated commit comparison. Review the draft and publish it manually. The workflow intentionally leaves it as a draft so breaking changes and contributor credit can be verified against the Release Checklist below first.

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
