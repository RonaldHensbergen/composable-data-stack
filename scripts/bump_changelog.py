#!/usr/bin/env python3
"""Date the `[Unreleased]` section of CHANGELOG.md for a version bump.

Used by .github/workflows/version-bump.yml to bundle the changelog dating
step into the same automated PR as the `pyproject.toml` version bump, so a
maintainer only needs to review/merge one PR and tag the release -- instead
of also hand-editing CHANGELOG.md before tagging.

This intentionally does NOT generate changelog *content* from commit
messages: entries under `[Unreleased]` are still hand-curated by each PR
that changes user-facing behaviour (see CONTRIBUTING.md). This script only
renames the existing `## [Unreleased]` heading to a dated
`## [X.Y.Z] - YYYY-MM-DD` heading and inserts a new empty `## [Unreleased]`
section above it, so future PRs have somewhere to add entries again.
"""
from __future__ import annotations

import datetime
import sys
from pathlib import Path

UNRELEASED_HEADING = "## [Unreleased]"


def bump_changelog(text: str, version: str, *, release_date: datetime.date) -> str:
    """Return `text` with `[Unreleased]` dated into a new `version` section.

    Raises ValueError if `text` has no `## [Unreleased]` heading, or if a
    `## [version]` heading already exists (bumping the same version twice).
    """
    if UNRELEASED_HEADING not in text:
        raise ValueError(f"CHANGELOG.md has no {UNRELEASED_HEADING!r} heading to date")

    dated_heading = f"## [{version}] - {release_date.isoformat()}"
    if f"## [{version}]" in text:
        raise ValueError(f"CHANGELOG.md already has a {dated_heading!r}-style heading")

    # Replace the first `## [Unreleased]` occurrence with a fresh empty
    # `## [Unreleased]` section followed by the same heading, now dated.
    return text.replace(
        UNRELEASED_HEADING,
        f"{UNRELEASED_HEADING}\n\n{dated_heading}",
        1,
    )


def main() -> int:
    args = sys.argv[1:]
    if len(args) != 1:
        print("Usage: bump_changelog.py <version>", file=sys.stderr)
        return 2

    version = args[0]
    repo_root = Path(__file__).resolve().parent.parent
    changelog_path = repo_root / "CHANGELOG.md"

    try:
        text = changelog_path.read_text(encoding="utf-8")
        updated = bump_changelog(text, version, release_date=datetime.date.today())
    except (OSError, ValueError) as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1

    changelog_path.write_text(updated, encoding="utf-8")
    print(f"OK: dated CHANGELOG.md [Unreleased] section as {version}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
