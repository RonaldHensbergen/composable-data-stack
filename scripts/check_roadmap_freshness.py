#!/usr/bin/env python3
"""Check whether docs/roadmap.md's issue references and version are stale.

docs/roadmap.md hand-curates issue numbers into sections with an implicit
contract: "Ready For Contributors" issues are claimed to be open and
unassigned, "Completed" issues are claimed to be shipped (closed), and
"Near-Term" issues that have since closed should probably be promoted to
"Completed". It also states a "current release" version that can drift from
`pyproject.toml`. Nothing enforces any of this, so the roadmap can silently
go stale. This script parses the roadmap, looks up the referenced issues'
actual state via the `gh` CLI, and reports mismatches.

Used by .github/workflows/roadmap-freshness.yml on a biweekly cadence; also
runnable locally: `python scripts/check_roadmap_freshness.py`.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

ISSUE_REF_RE = re.compile(r"#(\d+)")
SECTION_HEADING_RE = re.compile(r"^## (.+?)\s*$", re.MULTILINE)
STABLE_VERSION_RE = re.compile(r"current release \(v(\d+\.\d+\.\d+)\)")

READY_FOR_CONTRIBUTORS = "Ready For Contributors"
COMPLETED = "Completed"
NEAR_TERM = "Near-Term (Next 1\u20133 Releases)"


@dataclass(frozen=True)
class RoadmapEntry:
    """A single issue reference found under a tracked roadmap section."""

    number: int
    section: str


IssueFetcher = Callable[[str, "list[int]"], "dict[int, dict[str, object]]"]


def split_sections(roadmap_text: str) -> dict[str, str]:
    """Return a mapping of section heading -> section body text."""
    headings = list(SECTION_HEADING_RE.finditer(roadmap_text))
    sections: dict[str, str] = {}
    for index, match in enumerate(headings):
        start = match.end()
        end = headings[index + 1].start() if index + 1 < len(headings) else len(roadmap_text)
        sections[match.group(1)] = roadmap_text[start:end]
    return sections


def extract_entries(roadmap_text: str) -> list[RoadmapEntry]:
    """Extract issue references from the sections this check tracks."""
    sections = split_sections(roadmap_text)
    entries: list[RoadmapEntry] = []
    for section in (READY_FOR_CONTRIBUTORS, COMPLETED, NEAR_TERM):
        body = sections.get(section)
        if body is None:
            continue
        seen: set[int] = set()
        for match in ISSUE_REF_RE.finditer(body):
            number = int(match.group(1))
            if number in seen:
                continue
            seen.add(number)
            entries.append(RoadmapEntry(number=number, section=section))
    return entries


def extract_stable_version(roadmap_text: str) -> str | None:
    """Return the version claimed by the "Stable Components" section, if any."""
    match = STABLE_VERSION_RE.search(roadmap_text)
    return match.group(1) if match else None


def pyproject_version(pyproject_path: Path) -> str:
    data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    version: str = data["project"]["version"]
    return version


def fetch_issue_states_via_gh(repo: str, numbers: list[int]) -> dict[int, dict[str, object]]:
    """Look up issue state/assignees for each number via the `gh` CLI.

    Numbers that fail to resolve (deleted, transferred, or `gh` unavailable)
    are recorded with state "UNKNOWN" rather than raising, so a single bad
    reference doesn't hide every other finding.
    """
    gh_path = shutil.which("gh")
    if gh_path is None:
        return {number: {"state": "UNKNOWN", "assignees": []} for number in numbers}

    states: dict[int, dict[str, object]] = {}
    for number in numbers:
        try:
            result = subprocess.run(  # nosec B603  # noqa: S603
                [
                    gh_path,
                    "issue",
                    "view",
                    str(number),
                    "--repo",
                    repo,
                    "--json",
                    "number,state,assignees",
                ],
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
            )
            payload = json.loads(result.stdout)
            states[number] = {
                "state": payload.get("state", "UNKNOWN"),
                "assignees": payload.get("assignees", []),
            }
        except (
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
            FileNotFoundError,
            json.JSONDecodeError,
        ):
            states[number] = {"state": "UNKNOWN", "assignees": []}
    return states


# "Completed" entries commonly reference the pull request that shipped the
# work, not a tracking issue, so `gh issue view` reports its state as MERGED
# rather than CLOSED. Treat both as "done" for that section.
DONE_STATES = {"CLOSED", "MERGED"}


def check_entries(
    entries: list[RoadmapEntry],
    issue_states: dict[int, dict[str, object]],
) -> list[str]:
    """Return a human-readable mismatch message per stale roadmap entry."""
    problems: list[str] = []
    for entry in entries:
        info = issue_states.get(entry.number)
        if info is None or info.get("state") == "UNKNOWN":
            problems.append(
                f"#{entry.number} (section '{entry.section}'): could not resolve issue state"
            )
            continue

        state = info.get("state")
        assignees = info.get("assignees") or []

        if entry.section == READY_FOR_CONTRIBUTORS:
            if state != "OPEN":
                problems.append(
                    f"#{entry.number} (section '{READY_FOR_CONTRIBUTORS}'): "
                    f"listed as available but issue state is {state}"
                )
            elif assignees:
                names = ", ".join(a.get("login", "?") for a in assignees)
                problems.append(
                    f"#{entry.number} (section '{READY_FOR_CONTRIBUTORS}'): "
                    f"listed as unassigned but is assigned to {names}"
                )
        elif entry.section == COMPLETED:
            if state not in DONE_STATES:
                problems.append(
                    f"#{entry.number} (section '{COMPLETED}'): "
                    f"listed as shipped but issue state is {state}"
                )
        elif entry.section == NEAR_TERM:
            if state in DONE_STATES:
                problems.append(
                    f"#{entry.number} (section '{NEAR_TERM}'): "
                    f"issue is {state.lower()}; consider promoting it to 'Completed'"
                )
    return problems


def check_stable_version(roadmap_text: str, pyproject_path: Path) -> str | None:
    """Return a mismatch message if the roadmap's claimed version has drifted."""
    claimed = extract_stable_version(roadmap_text)
    if claimed is None:
        return None
    declared = pyproject_version(pyproject_path)

    def major_minor(version: str) -> tuple[str, str]:
        parts = version.split(".")
        return parts[0], parts[1] if len(parts) > 1 else "0"

    if major_minor(claimed) != major_minor(declared):
        return (
            f"'Stable Components' claims current release v{claimed}, but "
            f"pyproject.toml declares {declared}"
        )
    return None


def run_check(
    roadmap_path: Path,
    pyproject_path: Path,
    repo: str,
    fetch_issue_states: IssueFetcher = fetch_issue_states_via_gh,
) -> list[str]:
    """Run the full freshness check and return all problems found."""
    roadmap_text = roadmap_path.read_text(encoding="utf-8")
    entries = extract_entries(roadmap_text)
    issue_states = fetch_issue_states(repo, sorted({entry.number for entry in entries}))

    problems = check_entries(entries, issue_states)
    version_problem = check_stable_version(roadmap_text, pyproject_path)
    if version_problem:
        problems.append(version_problem)
    return problems


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    repo = "RonaldHensbergen/composable-data-stack"

    problems = run_check(
        repo_root / "docs" / "roadmap.md",
        repo_root / "pyproject.toml",
        repo,
    )

    if not problems:
        print("OK: docs/roadmap.md is consistent with current issue state and version.")
        return 0

    print("docs/roadmap.md appears stale:", file=sys.stderr)
    for problem in problems:
        print(f"::warning::{problem}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
