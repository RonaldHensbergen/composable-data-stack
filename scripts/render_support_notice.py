#!/usr/bin/env python3
"""Render the machine-generated "Support" section for a GitHub release.

Used by `.github/workflows/release.yml` so every published release
communicates its support status without relying on a maintainer to
remember to write it by hand. The policy itself lives in
`docs/security-support-policy.md`; this script only renders the standard
notice described there (Section 4, "End-of-support communication").
"""
from __future__ import annotations

import sys

POLICY_URL = (
    "https://github.com/RonaldHensbergen/composable-data-stack/blob/main/"
    "docs/security-support-policy.md"
)

# Deliberately short relative to the weekly release cadence in
# docs/release-strategy.md: long enough to give operators time to notice
# and apply a new release, short enough to keep the "single supported
# release" model in docs/security-support-policy.md meaningful. Revisit at
# the annual policy review (docs/security-support-policy.md#6-review-and-reassessment)
# if the release cadence changes.
GRACE_PERIOD_DAYS = 14


def render(tag_ref: str) -> str:
    version = tag_ref[1:] if tag_ref.startswith("v") else tag_ref
    return (
        "## Support\n\n"
        f"Release `{version}` is the currently supported CDS release. It "
        "remains supported until superseded by the next tagged release, "
        f"plus a {GRACE_PERIOD_DAYS}-day upgrade grace period during which "
        "a reported Critical/High severity vulnerability still receives an "
        "emergency release rather than being deferred solely to upgrading. "
        "CDS is pre-1.0 and currently supports only the latest release; see "
        f"the full policy at {POLICY_URL}.\n"
    )


def main() -> int:
    args = sys.argv[1:]
    if len(args) != 1:
        print("Usage: render_support_notice.py <tag-ref>", file=sys.stderr)
        return 2

    print(render(args[0]), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
