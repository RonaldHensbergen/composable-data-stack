#!/usr/bin/env python3
"""Verify that generated release notes include a "Support" section.

Used by `.github/workflows/release.yml` to fail fast if a release would be
published without the machine-generated support notice described in
`docs/security-support-policy.md` (Section 4, "End-of-support
communication"). This guards against future template or workflow changes
silently dropping the notice.
"""
from __future__ import annotations

import sys
from pathlib import Path

REQUIRED_HEADING = "## Support"


def check(notes_path: Path) -> str | None:
    """Return an error message if the support section is missing, else None."""
    if not notes_path.exists():
        return f"Release notes file not found: {notes_path}"

    content = notes_path.read_text(encoding="utf-8")
    if REQUIRED_HEADING not in content:
        return (
            f"Release notes at {notes_path} are missing a '{REQUIRED_HEADING}' "
            "section. See docs/security-support-policy.md for what this "
            "release must communicate."
        )

    return None


def main() -> int:
    args = sys.argv[1:]
    if len(args) != 1:
        print("Usage: check_release_notes_support.py <release-notes-path>", file=sys.stderr)
        return 2

    error = check(Path(args[0]))
    if error:
        print(f"::error::{error}", file=sys.stderr)
        return 1

    print(f"OK: {args[0]} includes a '{REQUIRED_HEADING}' section.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
