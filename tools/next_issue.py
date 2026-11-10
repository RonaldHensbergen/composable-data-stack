#!/usr/bin/env python3
"""Fetch the next issue from the Composable Data Stack Roadmap project.

Implements the "next issue" rule from AGENTS.md: pick the open issue with the
lowest `Order` value in GitHub Project "Composable Data Stack Roadmap"
(project 1, owner RonaldHensbergen), skipping:

- items with Project status `Done` or `In Progress`,
- issues that already have an open PR linked (connected or cross-referenced),
- issues that are assigned to someone,
- parent issues: anything with sub-issues or a task list in its body.

Usage:
    next_issue.py                 print the next pickable issue
    next_issue.py --json          same, as JSON
    next_issue.py --all           list all candidates in Order, with status
    next_issue.py --skip-first N  skip the first N pickable issues and print
                                  the (N+1)-th

Requires the `gh` CLI, authenticated against the repository.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess  # nosec B404
import sys

OWNER = "RonaldHensbergen"
REPO = "RonaldHensbergen/composable-data-stack"
PROJECT_NUMBER = 1

BLOCKED_STATUSES = {"done", "in progress"}
TASK_LIST_RE = re.compile(r"^\s*[-*]\s+\[[ xX]\]", re.MULTILINE)


GH = shutil.which("gh") or "gh"


def gh(*args: str) -> str:
    result = subprocess.run(  # nosec B603  # noqa: S603
        [GH, *args], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def fetch_project_items() -> list[dict]:
    out = gh(
        "project", "item-list", str(PROJECT_NUMBER),
        "--owner", OWNER, "--limit", "200", "--format", "json",
    )
    items = json.loads(out).get("items", [])
    issues = []
    for item in items:
        content = item.get("content") or {}
        number = content.get("number")
        if not number or content.get("type") != "Issue":
            continue
        issues.append({
            "number": number,
            "title": content.get("title", ""),
            "url": content.get("url", ""),
            "body": content.get("body") or "",
            "labels": item.get("labels", []),
            "milestone": (item.get("milestone") or {}).get("title", ""),
            "order": item.get("order"),
            "status": item.get("status", ""),
        })
    issues.sort(key=lambda i: i["order"] if i["order"] is not None else float("inf"))
    return issues


def has_open_pr(number: int) -> bool:
    """True if an open PR references/links this issue (timeline events)."""
    try:
        out = gh("api", "--paginate", f"repos/{REPO}/issues/{number}/timeline")
    except RuntimeError:
        return False
    for event in json.loads(out):
        if event.get("event") not in ("connected", "cross-referenced"):
            continue
        source = event.get("source", {}).get("issue")
        if source and source.get("pull_request") and source.get("state") == "open":
            return True
    return False


def assignees(number: int) -> list[str]:
    """Logins this issue is assigned to (empty if unassigned or on error)."""
    try:
        out = gh("api", f"repos/{REPO}/issues/{number}",
                "--jq", "[.assignees[].login]")
        return json.loads(out)
    except RuntimeError:
        return []


def sub_issue_count(number: int) -> int:
    try:
        out = gh("api", f"repos/{REPO}/issues/{number}/sub_issues")
        return len(json.loads(out))
    except RuntimeError:
        return 0


def skip_reason(issue: dict) -> str | None:
    """Return why this issue cannot be picked, or None if it is pickable."""
    if issue["status"].lower() in BLOCKED_STATUSES:
        return f"project status: {issue['status'] or 'unknown'}"
    if has_open_pr(issue["number"]):
        return "open PR exists"
    assigned = assignees(issue["number"])
    if assigned:
        return f"assigned to {', '.join(assigned)}"
    if sub_issue_count(issue["number"]) > 0:
        return "has sub-issues (parent issue)"
    if TASK_LIST_RE.search(issue["body"]):
        return "task list in body (parent issue)"
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="output JSON")
    parser.add_argument("--all", action="store_true",
                        help="list all project issues in Order with skip reasons")
    parser.add_argument("--skip-first", type=int, default=0, metavar="N",
                        help="skip the first N pickable issues")
    args = parser.parse_args()

    issues = fetch_project_items()

    if args.all:
        rows = []
        for issue in issues:
            reason = skip_reason(issue)
            rows.append({
                "order": issue["order"], "number": issue["number"],
                "status": issue["status"], "title": issue["title"],
                "pickable": reason is None, "skip_reason": reason,
            })
        print(json.dumps(rows, indent=2) if args.json else
              "\n".join(
                  f"#{r['number']} (order {r['order']}, {r['status']}): {r['title']}"
                  + ("  <- pickable" if r["pickable"] else f"  [skip: {r['skip_reason']}]")
                  for r in rows
              ))
        return 0

    skip_count = 0
    for issue in issues:
        reason = skip_reason(issue)
        if reason:
            continue
        if skip_count < args.skip_first:
            skip_count += 1
            continue
        if args.json:
            print(json.dumps(issue, indent=2))
        else:
            print(f"Next issue: #{issue['number']} (order {issue['order']})")
            print(f"Title:      {issue['title']}")
            print(f"Status:     {issue['status']}")
            print(f"Milestone:  {issue['milestone'] or '-'}")
            print(f"Labels:     {', '.join(issue['labels']) or '-'}")
            print(f"URL:        {issue['url']}")
        return 0

    print("No pickable issue found (all candidates are Done, In Progress, "
          "have an open PR, or are parent issues).", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
