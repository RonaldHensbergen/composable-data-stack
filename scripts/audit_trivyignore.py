#!/usr/bin/env python3
"""Report `.trivyignore` exceptions that are no longer needed.

Takes the Trivy JSON reports of the published images, scanned *without* the
ignore file, and compares the vulnerability IDs they contain with the
entries in `.trivyignore`. An entry whose ID no longer appears in any report
has been resolved (a fixed base image or package was published) and can be
removed. Entries close to their `exp:` date are listed too.

Usage: audit_trivyignore.py [--ignore FILE] [--warn-days N] [--output FILE]
                            REPORT.json [REPORT.json ...]

Exit status is 0 when the audit ran, whether or not entries were flagged;
the output file is written only when there is something to report.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

ENTRY_RE = re.compile(r"^(?P<id>\S+)\s+exp:(?P<exp>\d{4}-\d{2}-\d{2})\s*$")


@dataclass(frozen=True)
class Entry:
    vuln_id: str
    expires: date
    line: int


def parse_entries(text: str) -> list[Entry]:
    """Return the ignore entries, skipping comments and blank lines."""
    entries: list[Entry] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = ENTRY_RE.match(line)
        if match:
            entries.append(
                Entry(match["id"], date.fromisoformat(match["exp"]), number)
            )
    return entries


def reported_ids(report_texts: list[str]) -> set[str]:
    """Collect every vulnerability ID from Trivy JSON reports."""
    ids: set[str] = set()
    for text in report_texts:
        for result in json.loads(text).get("Results") or []:
            for vuln in result.get("Vulnerabilities") or []:
                ids.add(vuln["VulnerabilityID"])
    return ids


def audit(
    entries: list[Entry], reported: set[str], today: date, warn_days: int
) -> tuple[list[Entry], list[Entry]]:
    """Split entries into (resolved, expiring-soon-but-still-needed)."""
    resolved = [entry for entry in entries if entry.vuln_id not in reported]
    horizon = today + timedelta(days=warn_days)
    expiring = [
        entry
        for entry in entries
        if entry.vuln_id in reported and entry.expires <= horizon
    ]
    return resolved, expiring


def render_report(
    resolved: list[Entry], expiring: list[Entry], images_scanned: int
) -> str:
    lines = ["## `.trivyignore` audit", ""]
    if resolved:
        lines += [
            "### Resolved exceptions: remove from `.trivyignore`",
            "",
            f"These IDs no longer appear in any of the {images_scanned} published "
            "images, so a fix has shipped. Confirm the next image rebuild is "
            "clean, then delete each entry and its comment block.",
            "",
        ]
        lines += [
            f"- `{e.vuln_id}` (line {e.line}, expires {e.expires})" for e in resolved
        ]
        lines.append("")
    if expiring:
        lines += [
            "### Expiring soon and still reported",
            "",
            "Fix these before the expiry or the daily scan fails again.",
            "",
        ]
        lines += [f"- `{e.vuln_id}` expires {e.expires}" for e in expiring]
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("reports", nargs="+", type=Path)
    parser.add_argument("--ignore", type=Path, default=Path(".trivyignore"))
    parser.add_argument("--warn-days", type=int, default=14)
    parser.add_argument("--output", type=Path, default=Path("trivyignore-audit.md"))
    args = parser.parse_args(argv)

    try:
        entries = parse_entries(args.ignore.read_text(encoding="utf-8"))
        reported = reported_ids(
            [path.read_text(encoding="utf-8") for path in args.reports]
        )
    except (OSError, ValueError, KeyError) as exc:
        print(f"Cannot audit {args.ignore}: {exc}", file=sys.stderr)
        return 1

    resolved, expiring = audit(entries, reported, date.today(), args.warn_days)
    if not resolved and not expiring:
        print("All .trivyignore entries are still needed.")
        return 0
    report = render_report(resolved, expiring, len(args.reports))
    args.output.write_text(report, encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
