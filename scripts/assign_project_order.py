#!/usr/bin/env python3
"""Compute the next project `Order` value for an issue in the roadmap project.

Used by .github/workflows/issue-project-order.yml: given an issue's metadata
(`gh issue view --json number,title,labels,milestone`) and the project's
items (`gh project item-list --format json`), print the next free `Order`
for that issue.

The assigned order reflects priority via auto-assign inbox bands (see
AGENT.md, "Project `Order` tiers"): the issue's category is picked by
precedence — fix, milestone, security, priority:high, priority:medium,
rest — and the issue appends at max+1 inside its band (band base when the
band is empty), so existing items are never reordered.

Usage:
    assign_project_order.py <issue.json> <items.json>
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# Auto-assign inbox bands, ordered by priority. Bands sit above the curated
# board (currently max 700) so newly assigned items never interleave with
# existing, manually ordered items.
BANDS: dict[str, int] = {
    "fix": 1000,
    "milestone": 1100,
    "security": 1200,
    "high": 1300,
    "medium": 1400,
    "rest": 1500,
}
BAND_WIDTH = 100
FIX_TITLE_RE = re.compile(r"^fix(\(.+\))?!?:", re.IGNORECASE)
SECURITY_LABELS = {"area:security", "vuln-scan"}


def label_names(labels: list | None) -> set[str]:
    return {label["name"] if isinstance(label, dict) else label for label in (labels or [])}


def category(labels: list | None, milestone_title: str, title: str) -> str:
    """Pick the priority category by precedence: fix > milestone > security >
    priority:high > priority:medium > rest."""
    names = label_names(labels)
    if "bug" in names or FIX_TITLE_RE.match(title or ""):
        return "fix"
    if milestone_title:
        return "milestone"
    if names & SECURITY_LABELS:
        return "security"
    if "priority:high" in names:
        return "high"
    if "priority:medium" in names:
        return "medium"
    return "rest"


def assign(issue: dict, items: list[dict]) -> tuple[str, int]:
    """Return (category, next order) for `issue` given the project `items`."""
    cat = category(
        issue.get("labels"),
        (issue.get("milestone") or {}).get("title") or "",
        issue.get("title") or "",
    )
    base = BANDS[cat]
    in_band = [
        item["order"]
        for item in items
        if (item.get("content") or {}).get("type") == "Issue"
        and item.get("order") is not None
        and base <= item["order"] < base + BAND_WIDTH
    ]
    assigned = max(in_band) + 1 if in_band else base
    if assigned >= base + BAND_WIDTH:
        # Band full: fall back to appending after the global max.
        all_orders = [item["order"] for item in items if item.get("order") is not None]
        assigned = (max(all_orders) + 1) if all_orders else base
    return cat, assigned


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("issue_json", type=Path, help="`gh issue view --json` output for the issue")
    parser.add_argument("items_json", type=Path, help="`gh project item-list --format json` output")
    args = parser.parse_args()
    issue = json.loads(args.issue_json.read_text(encoding="utf-8"))
    items = json.loads(args.items_json.read_text(encoding="utf-8"))["items"]
    cat, order = assign(issue, items)
    print(f"category={cat} assigned_order={order}", file=sys.stderr)
    print(order)
    return 0


if __name__ == "__main__":
    sys.exit(main())
