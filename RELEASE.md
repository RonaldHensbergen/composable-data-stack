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

1. Create a release branch (optional for larger release prep):

```bash
git checkout -b release/vX.Y.Z
```

1. Update version in pyproject.toml and changelog.

1. Run tests:

```bash
python -m unittest discover -s tests -p "*.py"
```

1. Commit release metadata:

```bash
git add pyproject.toml CHANGELOG.md
git commit -m "Release vX.Y.Z"
```

1. Tag and push:

```bash
git tag vX.Y.Z
git push origin main --tags
```

1. Create GitHub release notes from CHANGELOG entries.

## Rollback

If a release is broken:

1. Document issue in release notes
2. Publish hotfix release vX.Y.Z+1
3. Backfill tests for the regression
