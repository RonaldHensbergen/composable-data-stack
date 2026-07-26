#!/usr/bin/env python3
"""Verify that a release tag matches the version declared in pyproject.toml.

Used by .github/workflows/release.yml to fail fast when a `vX.Y.Z` tag is
pushed without first bumping the `project.version` field in pyproject.toml.
"""
from __future__ import annotations

import sys
import tomllib
from pathlib import Path

from packaging.version import InvalidVersion, Version


def pyproject_version(pyproject_path: Path) -> str:
    data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    try:
        return data["project"]["version"]
    except KeyError as exc:
        raise SystemExit(f"pyproject.toml is missing [project].version: {exc}") from exc


def check(tag_ref: str, pyproject_path: Path) -> str | None:
    """Return an error message if the tag and pyproject.toml versions mismatch, else None."""
    tag_version = tag_ref[1:] if tag_ref.startswith("v") else tag_ref
    declared_version = pyproject_version(pyproject_path)

    try:
        parsed_tag = Version(tag_version)
    except InvalidVersion:
        return f"Tag ref '{tag_ref}' is not a valid version (expected e.g. 'v1.2.3' or 'v1.2.3-alpha')"

    try:
        parsed_pyproject = Version(declared_version)
    except InvalidVersion:
        return f"pyproject.toml version '{declared_version}' is not a valid PEP 440 version"

    if parsed_tag != parsed_pyproject:
        return (
            f"Tag version '{tag_version}' does not match pyproject.toml version "
            f"'{declared_version}'. Bump pyproject.toml before tagging a release."
        )

    return None


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: check_release_version.py <tag-ref>", file=sys.stderr)
        return 2

    repo_root = Path(__file__).resolve().parent.parent
    error = check(sys.argv[1], repo_root / "pyproject.toml")
    if error:
        print(f"::error::{error}", file=sys.stderr)
        return 1

    print(f"OK: release tag '{sys.argv[1]}' matches pyproject.toml version.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
