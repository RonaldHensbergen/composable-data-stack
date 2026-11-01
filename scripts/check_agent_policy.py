#!/usr/bin/env python3
"""Enforce the agent approval policy on protected paths (issue #1084).

Runtime guardrail for agent-produced changes, complementing the static
guardrails (prompt lint, redaction, branch protection): any commit carrying
the agent co-author footer that touches a protected path requires explicit
human approval, expressed as the `agent-approved` label on the PR. Without
the label, CI fails.

Protected paths are the ones where an agent mistake is highest-impact:
CI/workflows, prompts, security tooling, dependency/policy config, certs,
and the agent's own instructions.

Runs in the CI lint job on pull requests (needs fetch-depth: 0). Locally:
    python scripts/check_agent_policy.py --base origin/main --head HEAD
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

AGENT_FOOTER = "Co-Authored-By: Mistral Vibe"
APPROVAL_LABEL = "agent-approved"

# Paths agents must not change without human approval (label on the PR).
PROTECTED_PATHS: tuple[str, ...] = (
    ".github/workflows/",
    ".github/prompts/",
    ".github/CODEOWNERS",
    ".github/dependabot.yml",
    ".github/pull_request_template.md",
    ".github/ISSUE_TEMPLATE/",
    ".pre-commit-config.yaml",
    "certs/",
    "scripts/check_prompts.py",
    "scripts/redact_secrets.py",
    "renovate.json",
    "sonar-project.properties",
    "AGENT.md",
    "docs/maintainer-merge-policy.md",
    "docs/security-rules.md",
)


def _run(cmd: list[str]) -> tuple[int, str]:
    result = subprocess.run(  # nosec B603  # noqa: S603
        cmd, cwd=REPO_ROOT, capture_output=True, text=True
    )
    return result.returncode, result.stdout


def is_agent_commit(message: str) -> bool:
    return AGENT_FOOTER in message


def protected_hit(path: str) -> str | None:
    for protected in PROTECTED_PATHS:
        if path == protected or path.startswith(protected):
            return protected
    return None


def agent_commits(base: str, head: str) -> list[str]:
    """Return SHAs of commits in base..head whose message carries the agent footer."""
    rc, out = _run(["git", "log", "--format=%H%x00%B%x01", f"{base}..{head}"])
    if rc != 0:
        return []
    shas = []
    for record in out.split("\x01"):
        if "\x00" not in record:
            continue
        sha, body = record.split("\x00", 1)
        if sha.strip() and is_agent_commit(body):
            shas.append(sha.strip())
    return shas


def touched_files(shas: list[str]) -> set[str]:
    files: set[str] = set()
    for sha in shas:
        rc, out = _run(["git", "show", "--name-only", "--format=", sha])
        if rc == 0:
            files.update(line.strip() for line in out.splitlines() if line.strip())
    return files


def pr_labels() -> list[str]:
    """Labels of the current PR when running in CI, else empty."""
    ref = os.environ.get("GITHUB_REF", "")
    match = re.search(r"refs/pull/(\d+)/", ref)
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not match or not repo:
        return []
    rc, out = _run(["gh", "pr", "view", match.group(1), "--repo", repo,
                    "--json", "labels", "--jq", ".labels[].name"])
    if rc != 0:
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def check(base: str, head: str, labels: list[str]) -> list[str]:
    errors: list[str] = []
    commits = agent_commits(base, head)
    if not commits:
        return errors
    hits = sorted({p for f in touched_files(commits) if (p := protected_hit(f))})
    if hits and APPROVAL_LABEL not in labels:
        errors.append(
            f"agent-authored commits touch protected path(s): {', '.join(hits)}. "
            f"Add the `{APPROVAL_LABEL}` label to this PR (human approval) or revert "
            f"those changes. See AGENT.md 'Agent approval policy'."
        )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--labels", default="", help="comma-separated PR labels (default: query gh in CI)")
    args = parser.parse_args()
    labels = [l.strip() for l in args.labels.split(",") if l.strip()] if args.labels else pr_labels()
    errors = check(args.base, args.head, labels)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("agent approval policy OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
