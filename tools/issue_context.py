#!/usr/bin/env python3
"""One-call issue bootstrap for RonaldHensbergen/composable-data-stack.

Usage:
    issue_context.py <issue-number> [--json]

Prints a compact bundle — issue fields, roadmap Order/status, linked PRs,
sub-issues, comments, body — that otherwise takes 5+ separate gh calls.
Requires the `gh` CLI, authenticated.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess  # nosec B404

OWNER = "RonaldHensbergen"
REPO = f"{OWNER}/composable-data-stack"


GH = shutil.which("gh") or "gh"


def gh(*args: str) -> str:
    r = subprocess.run(  # nosec B603  # noqa: S603
        [GH, *args], capture_output=True, text=True, check=False
    )
    if r.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)} failed: {r.stderr.strip()}")
    return r.stdout


def trunc(s: str, limit: int = 400) -> str:
    s = (s or "").strip()
    return s if len(s) <= limit else s[: limit - 1] + "…"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("number", type=int)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    n = args.number

    issue = json.loads(gh(
        "issue", "view", str(n), "--repo", REPO, "--json",
        "number,title,state,url,body,labels,milestone,assignees,author,comments",
    ))

    # roadmap context (Order/status) from the project
    order = status = None
    try:
        items = json.loads(gh("project", "item-list", "1", "--owner", OWNER,
                              "--limit", "200", "--format", "json")).get("items", [])
        for item in items:
            c = item.get("content") or {}
            if c.get("type") == "Issue" and c.get("number") == n:
                order, status = item.get("order"), item.get("status")
                break
    except RuntimeError:
        pass

    # linked PRs via timeline events
    linked_prs = []
    try:
        for ev in json.loads(gh("api", "--paginate", f"repos/{REPO}/issues/{n}/timeline")):
            if ev.get("event") not in ("connected", "cross-referenced"):
                continue
            src = (ev.get("source") or {}).get("issue") or {}
            if src.get("pull_request"):
                linked_prs.append(f"#{src['number']} ({src.get('state')}): {src.get('title')}")
    except RuntimeError:
        pass

    sub_issues = []
    try:
        sub_issues = json.loads(gh(
            "api", f"repos/{REPO}/issues/{n}/sub_issues",
            "--jq", '[.[] | {number, title, state}]'))
    except RuntimeError:
        pass

    if args.json:
        print(json.dumps({
            "issue": issue, "order": order, "project_status": status,
            "linked_prs": linked_prs, "sub_issues": sub_issues,
        }, indent=2))
        return 0

    labels = ", ".join(l["name"] for l in issue.get("labels", [])) or "-"
    assignees = ", ".join(a["login"] for a in issue.get("assignees", [])) or "none"
    milestone = (issue.get("milestone") or {}).get("title") or "-"
    author = (issue.get("author") or {}).get("login", "?")
    print(f"#{issue['number']} {issue['title']}")
    print(f"state={issue['state']} author={author} labels={labels}")
    print(f"milestone={milestone} assignees={assignees} roadmap order={order} status={status}")
    print(f"url={issue['url']}")
    print(f"linked PRs: {', '.join(linked_prs) or 'none'}")
    print(f"sub-issues ({len(sub_issues)}): "
          + ", ".join(f"#{s['number']} {s['title']} ({s['state']})" for s in sub_issues))
    comments = issue.get("comments", [])
    print(f"comments ({len(comments)}):")
    for c in comments[-5:]:
        login = (c.get("author") or {}).get("login", "?")
        print(f"  @{login} {str(c.get('createdAt', ''))[:10]}: {trunc(c.get('body', ''), 300)}")
    print("--- body ---")
    print(issue.get("body") or "(empty)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
