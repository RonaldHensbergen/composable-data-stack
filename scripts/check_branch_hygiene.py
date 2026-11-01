#!/usr/bin/env python3
"""Branch hygiene audit: which branches and worktrees carry no meaningful
changes anymore (i.e. their work is already merged into origin/main, in the
same or an evolved form).

Classification per branch:

- `merged-ancestor` — the branch tip is an ancestor of origin/main: nothing
  unique at all. Safe to delete.
- `merged-patch-equivalent` — every commit's patch already exists on main
  (``git cherry`` shows no unique patches; typical for squash merges). Safe
  to delete.
- `active-pr` — the branch is the head of an open pull request. Not stale.
- `open-work` — commits whose patches are not on main and no open PR: worth
  a human look before deleting.

Used by .github/workflows/branch-hygiene.yml (daily) to file a hygiene
report issue; also runnable locally: `python scripts/check_branch_hygiene.py`
(or `make branch-hygiene`). Locally, pass --local to also audit local
branches and worktrees (CI runners only see remote refs).

The script only reports; it never deletes anything.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MAIN = "origin/main"
IN_CI = bool(os.environ.get("GITHUB_ACTIONS"))


def _run(cmd: list[str]) -> tuple[int, str]:
    result = subprocess.run(  # nosec B603  # noqa: S603
        cmd, cwd=REPO_ROOT, capture_output=True, text=True
    )
    return result.returncode, result.stdout


def _remote_branches() -> list[str]:
    rc, out = _run(["git", "for-each-ref", "--format=%(refname)", "refs/remotes/origin/"])
    refs = [r for r in out.splitlines() if r and not r.endswith("/HEAD")]
    return sorted(set(refs))


def _local_branches() -> list[str]:
    rc, out = _run(["git", "for-each-ref", "--format=%(refname)", "refs/heads/"])
    return sorted(r for r in out.splitlines() if r)


def _short_name(ref: str) -> str:
    return ref.replace("refs/remotes/origin/", "").replace("refs/heads/", "")


def _open_pr_heads() -> dict[str, tuple[int, str]]:
    """Map head branch name -> (PR number, title) for open PRs, if gh works."""
    rc, out = _run(["gh", "pr", "list", "--state", "open", "--limit", "200",
                    "--json", "headRefName,number,title", "--jq",
                    ".[] | [.headRefName, (.number | tostring), .title] | @tsv"])
    if rc != 0 or not out.strip():
        return {}
    prs: dict[str, tuple[int, str]] = {}
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            prs[parts[0]] = (int(parts[1]), parts[2])
    return prs


def classify(ref: str, prs: dict[str, tuple[int, str]]) -> tuple[str, str]:
    """Return (classification, detail) for a branch ref."""
    short = _short_name(ref)
    if short == "main":
        return ("skip", "main")
    if short in prs:
        number, title = prs[short]
        return ("active-pr", f"PR #{number}: {title}")
    rc, _ = _run(["git", "merge-base", "--is-ancestor", ref, MAIN])
    if rc == 0:
        return ("merged-ancestor", "tip is an ancestor of origin/main")
    _, cherry = _run(["git", "cherry", MAIN, ref])
    unique = [line[1:] for line in cherry.splitlines() if line.startswith("+")]
    if not unique:
        return ("merged-patch-equivalent", "all patches exist on origin/main (git cherry)")
    _, log = _run(["git", "log", f"{MAIN}..{ref}", "--oneline"])
    subjects = "; ".join(log.splitlines()[:5])
    return ("open-work", f"{len(unique)} unique patch(es): {subjects}")


def _worktrees() -> list[tuple[str, str, str]]:
    """Return (path, head sha, branch short name) for each worktree."""
    rc, out = _run(["git", "worktree", "list", "--porcelain"])
    entries: list[tuple[str, str, str]] = []
    path = sha = branch = None
    for line in out.splitlines() + [""]:
        if line.startswith("worktree "):
            if path:
                entries.append((path, sha or "", branch or ""))
            path, sha, branch = line[len("worktree "):], None, None
        elif line.startswith("HEAD "):
            sha = line[len("HEAD "):]
        elif line.startswith("branch "):
            branch = _short_name(line[len("branch "):])
        elif not line and path:
            entries.append((path, sha or "", branch or ""))
            path = None
    return entries


def render_report(include_local: bool, fetch: bool = True) -> str:
    if fetch and not IN_CI:
        _run(["git", "fetch", "--prune", "origin"])
    prs = _open_pr_heads()

    rows: list[tuple[str, str, str, str]] = []  # (name, class, detail, scope)
    for ref in _remote_branches():
        cls, detail = classify(ref, prs)
        rows.append((_short_name(ref), cls, detail, "remote"))
    if include_local:
        remote_names = {_short_name(r) for r in _remote_branches()}
        for ref in _local_branches():
            name = _short_name(ref)
            if name in remote_names:
                continue  # already reported via its remote ref
            cls, detail = classify(ref, prs)
            rows.append((name, cls, detail, "local"))

    stale = [r for r in rows if r[1].startswith("merged")]
    active = [r for r in rows if r[1] == "active-pr"]
    open_work = [r for r in rows if r[1] == "open-work"]

    lines = [
        "# Branch hygiene report",
        "",
        f"STALE_BRANCHES: {len(stale)}",
        "",
        "Generated by `scripts/check_branch_hygiene.py`; filed by `branch-hygiene.yml`.",
        "A branch is stale when all its work is already on `origin/main`",
        "(ancestor of main, or every patch present per `git cherry`).",
        "",
        f"- stale (merged, safe to delete): **{len(stale)}**",
        f"- active PR branches: **{len(active)}**",
        f"- open work needing a look: **{len(open_work)}**",
        "",
    ]
    if stale:
        lines += ["## Stale branches (merged into main)", "",
                  "| Branch | Scope | Why stale | Delete with |", "| --- | --- | --- | --- |"]
        for name, cls, detail, scope in stale:
            cmd = f"git push origin --delete {name}" if scope == "remote" else f"git branch -D {name}"
            lines.append(f"| `{name}` | {scope} | {cls}: {detail} | `{cmd}` |")
        lines.append("")
    if active:
        lines += ["## Active PR branches (not stale)", "", "| Branch | PR |", "| --- | --- |"]
        for name, _, detail, _ in active:
            lines.append(f"| `{name}` | {detail} |")
        lines.append("")
    if open_work:
        lines += ["## Open work (unique patches not on main, no open PR)", "",
                  "| Branch | Scope | Unique commits |", "| --- | --- | --- |"]
        for name, _, detail, scope in open_work:
            lines.append(f"| `{name}` | {scope} | {detail} |")
        lines.append("")
    if include_local and not IN_CI:
        lines += ["## Worktrees (local)", "", "| Worktree | Branch | Classification |", "| --- | --- | --- |"]
        for path, _sha, branch in _worktrees():
            if not branch or branch == "main":
                cls = "main worktree"
            else:
                ref = f"refs/heads/{branch}"
                cls, _ = classify(ref, prs)
            lines.append(f"| `{path}` | `{branch or 'detached'}` | {cls} |")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--local", action="store_true",
                        help="also audit local-only branches and worktrees (default in CI: off)")
    parser.add_argument("--no-fetch", action="store_true", help="skip git fetch (local runs)")
    args = parser.parse_args()
    include_local = args.local or not IN_CI
    print(render_report(include_local, fetch=not args.no_fetch))
    return 0


if __name__ == "__main__":
    sys.exit(main())
