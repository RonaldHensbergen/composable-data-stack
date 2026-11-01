#!/usr/bin/env python3
"""Biweekly audit of the adopted Impatient Programming practices.

Rolls the repo-enforceable IMP signals into one report:

- contract coverage (from scripts/check_contract_coverage.py --stdout),
- the commit mix of the last 14 days, so `fix:` vs `refine:` labeling stays
  honest (a fix means a defect in code that is live; everything else is
  refinement),
- open PRs labeled `automerge` (green merges itself, where gated),
- the latest kill-matrix (mutation-tests.yml) workflow run,
- whether the IMP rules page itself changed, detected against the pinned
  sha256 in scripts/imp-rules.sha256 (the workflow files a tracking issue
  to re-read the rules when it drifts).

Used by .github/workflows/practices-audit.yml on a biweekly cadence; the
workflow files the report as an issue labeled `practices-audit`. Also
runnable locally: `python scripts/check_practices_audit.py` (GitHub sections
are skipped when `gh` is unavailable or unauthenticated), and
`python scripts/check_practices_audit.py --pin` re-pins the rules hash after
the change has been read and handled.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import Request, urlopen

REPO_ROOT = Path(__file__).resolve().parent.parent
COVERAGE_SCRIPT = REPO_ROOT / "scripts" / "check_contract_coverage.py"
OVERALL_RE = re.compile(r"\*\*Overall contract coverage:.*?\*\*.*")
COMMIT_PREFIXES = ["feat", "fix", "refine", "refactor", "perf", "docs", "test", "chore"]
SINCE = "14 days ago"

RULES_URL = "https://impatientprogramming.org/rules/"
RULES_HASH_FILE = REPO_ROOT / "scripts" / "imp-rules.sha256"
RULES_CHANGED_MARKER = "IMP rules CHANGED"
CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _run(cmd: list[str]) -> str | None:
    try:
        result = subprocess.run(  # nosec B603  # noqa: S603
            cmd, capture_output=True, text=True, check=True
        )
        return result.stdout
    except (OSError, subprocess.CalledProcessError):
        return None


def _repo() -> str | None:
    if os.environ.get("GITHUB_REPOSITORY"):
        return os.environ["GITHUB_REPOSITORY"]
    url = _run(["git", "-C", str(REPO_ROOT), "remote", "get-url", "origin"])
    if not url:
        return None
    url = url.strip().rstrip("/")
    match = re.search(r"[:/]([^/:]+/[^/]+?)(?:\.git)?$", url)
    return match.group(1) if match else None


def _contract_coverage() -> str:
    out = _run([sys.executable, str(COVERAGE_SCRIPT), "--stdout"])
    if out:
        match = OVERALL_RE.search(out)
        if match:
            return match.group(0)
    return "_contract coverage report unavailable (run `make contract-coverage`)_"


def _commit_mix() -> tuple[list[tuple[str, int]], int]:
    out = _run(["git", "-C", str(REPO_ROOT), "log", f"--since={SINCE}", "--format=%s"])
    subjects = out.splitlines() if out else []
    counts = {prefix: 0 for prefix in COMMIT_PREFIXES}
    other = 0
    for subject in subjects:
        prefix = subject.split(":", 1)[0].split("(", 1)[0].strip().lower()
        if prefix in counts:
            counts[prefix] += 1
        else:
            other += 1
    rows = [(p, c) for p, c in counts.items() if c] + [("other (unconventional)", other)] if other else [
        (p, c) for p, c in counts.items() if c
    ]
    return rows, len(subjects)


def _gh_json(args: list[str], fields: list[str]) -> object | None:
    repo = _repo()
    if not repo:
        return None
    out = _run(["gh", *args, "--repo", repo, "--json", ",".join(fields), "--jq", "."])
    if not out:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return None


def _sanitize_markdown_text(text: object) -> str:
    """Neutralize untrusted text (e.g. attacker-controlled PR titles) before
    it is persisted into a markdown report: single line, control chars
    stripped, markdown-significant characters escaped."""
    cleaned = CONTROL_CHARS_RE.sub("", str(text))
    cleaned = cleaned.replace("\\", "\\\\").replace("`", "\\`")
    cleaned = " ".join(cleaned.split())
    return cleaned


def _automerge_section() -> list[str]:
    data = _gh_json(["pr", "list", "--state", "open", "--label", "automerge", "--limit", "50"], ["number", "title"])
    if data is None:
        return ["_gh CLI unavailable or unauthenticated; skipping GitHub sections._"]
    prs = data if isinstance(data, list) else []
    lines = [f"{len(prs)} open PR(s) labeled `automerge`:"]
    for pr in prs:
        lines.append(f"- #{pr.get('number')} {_sanitize_markdown_text(pr.get('title', ''))}")
    if not prs:
        lines.append("- none")
    return lines


def _kill_matrix_section() -> list[str]:
    data = _gh_json(
        ["run", "list", "--workflow", "mutation-tests.yml", "--limit", "1"],
        ["databaseId", "status", "conclusion", "createdAt", "url"],
    )
    if data is None:
        return ["_gh CLI unavailable or unauthenticated; skipping GitHub sections._"]
    runs = data if isinstance(data, list) else []
    if not runs:
        return ["No mutation-tests.yml runs yet."]
    run = runs[0]
    conclusion = run.get("conclusion") or run.get("status")
    return [
        f"Latest kill-matrix run: **{conclusion}** ({run.get('createdAt', '?')})",
        f"- {run.get('url', '')}",
        "- Full per-mutant results are in that run's `mutmut-kill-matrix` artifact.",
    ]


def _fetch_rules() -> str | None:
    request = Request(RULES_URL, headers={"User-Agent": "cds-practices-audit/1.0"})  # nosec B310  # noqa: S310
    try:
        with urlopen(request, timeout=30) as response:  # nosec B310 - fixed https host  # noqa: S310
            return response.read().decode("utf-8", errors="replace")
    except OSError:
        return None


def _rules_hash(text: str) -> str:
    normalized = "\n".join(line.rstrip() for line in text.replace("\r\n", "\n").split("\n"))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _pinned_rules_hash() -> str | None:
    if not RULES_HASH_FILE.exists():
        return None
    fields = RULES_HASH_FILE.read_text(encoding="utf-8").splitlines()[0].split()
    return fields[0] if fields else None


def pin_rules_hash() -> int:
    text = _fetch_rules()
    if text is None:
        print(f"Could not fetch {RULES_URL}", file=sys.stderr)
        return 1
    digest = _rules_hash(text)
    RULES_HASH_FILE.write_text(f"{digest}  impatientprogramming.org/rules/\n", encoding="utf-8")
    print(f"Pinned {RULES_HASH_FILE.relative_to(REPO_ROOT)} = {digest}")
    return 0


def _rules_section() -> list[str]:
    text = _fetch_rules()
    if text is None:
        return [f"_Rules page {RULES_URL} unreachable; change detection skipped._"]
    current = _rules_hash(text)
    pinned = _pinned_rules_hash()
    if pinned is None:
        return ["_No pinned hash yet; run `make practices-audit-pin` to start tracking._"]
    if current == pinned:
        return [f"IMP rules unchanged (sha256 `{current[:12]}…` matches `scripts/imp-rules.sha256`)."]
    return [
        f"**{RULES_CHANGED_MARKER}**: pinned sha256 `{pinned[:12]}…`, live page hashes to `{current[:12]}…`.",
        f"- Rules page: {RULES_URL}",
        "- `practices-audit.yml` files a tracking issue to re-read the rules and re-assess",
        "  the adoption (CONTRIBUTING.md \"How We Work\" and the implemented mechanisms).",
        "- After handling it, re-pin with `make practices-audit-pin`; the next audit run",
        "  closes the tracking issue once the hashes match again.",
    ]


def render_report() -> str:
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    rows, total = _commit_mix()
    fix = dict(rows).get("fix", 0)
    refine = dict(rows).get("refine", 0)
    labeled = fix + refine
    refine_share = f" ({round(100 * refine / labeled)}% of fix+refine)" if labeled else ""
    lines = [
        f"# Impatient Programming practices audit — {now}",
        "",
        "Generated by `scripts/check_practices_audit.py`; filed by `practices-audit.yml`.",
        "",
        "## Contract coverage",
        "",
        _contract_coverage(),
        "",
        "Full table: `docs/contract-coverage.md` (CI fails when it is stale; weekly drift reports come from `contract-coverage.yml`).",
        "",
        f"## Commit mix, last 14 days ({total} commits)",
        "",
        "| Conventional prefix | Commits |",
        "| --- | --- |",
    ]
    lines += [f"| {prefix} | {count} |" for prefix, count in rows]
    lines += [
        "",
        f"`fix:` means a defect in live code; `refine:` is polishing of unshipped work{refine_share}.",
        "",
        "## Auto-merge usage (rule 24)",
        "",
    ]
    lines += _automerge_section()
    lines += ["", "## Kill matrix (rule 26)", ""]
    lines += _kill_matrix_section()
    lines += ["", "## IMP rules (source method)", ""]
    lines += _rules_section()
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--pin",
        action="store_true",
        help="pin the current sha256 of the IMP rules page into scripts/imp-rules.sha256",
    )
    args = parser.parse_args()
    if args.pin:
        return pin_rules_hash()
    print(render_report())
    return 0


if __name__ == "__main__":
    sys.exit(main())
