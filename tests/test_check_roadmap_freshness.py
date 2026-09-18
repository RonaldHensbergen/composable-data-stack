import importlib.util
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_roadmap_freshness.py"

_spec = importlib.util.spec_from_file_location("check_roadmap_freshness", SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
check_roadmap_freshness = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = check_roadmap_freshness
_spec.loader.exec_module(check_roadmap_freshness)


SAMPLE_ROADMAP = """# CDS Roadmap

## Ready For Contributors

- **Self-contained, low-risk**:
  - #101 Fix a small bug
  - #102 Improve an error message

## Stable Components

These are considered production-ready in the current release (v0.9.0):

- `cds validate`

## Near-Term (Next 1\u20133 Releases)

- \U0001F4CB **Some future item** (#201)
- \U0001F4CB **Another item** (#202)

## Completed

- \u2705 **Shipped thing** (#301)
- \u2705 **Another shipped thing** (#302, #303)
"""


class RoadmapEntryExtractionTest(unittest.TestCase):
    def test_extracts_entries_from_each_tracked_section(self) -> None:
        entries = check_roadmap_freshness.extract_entries(SAMPLE_ROADMAP)
        by_number = {entry.number: entry.section for entry in entries}
        self.assertEqual(
            by_number,
            {
                101: check_roadmap_freshness.READY_FOR_CONTRIBUTORS,
                102: check_roadmap_freshness.READY_FOR_CONTRIBUTORS,
                201: check_roadmap_freshness.NEAR_TERM,
                202: check_roadmap_freshness.NEAR_TERM,
                301: check_roadmap_freshness.COMPLETED,
                302: check_roadmap_freshness.COMPLETED,
                303: check_roadmap_freshness.COMPLETED,
            },
        )

    def test_extracts_stable_version(self) -> None:
        self.assertEqual(check_roadmap_freshness.extract_stable_version(SAMPLE_ROADMAP), "0.9.0")

    def test_missing_version_returns_none(self) -> None:
        self.assertIsNone(check_roadmap_freshness.extract_stable_version("# no version here"))


class CheckEntriesTest(unittest.TestCase):
    def test_ready_for_contributors_open_and_unassigned_passes(self) -> None:
        entries = [
            check_roadmap_freshness.RoadmapEntry(101, check_roadmap_freshness.READY_FOR_CONTRIBUTORS)
        ]
        states = {101: {"state": "OPEN", "assignees": []}}
        self.assertEqual(check_roadmap_freshness.check_entries(entries, states), [])

    def test_ready_for_contributors_closed_is_flagged(self) -> None:
        entries = [
            check_roadmap_freshness.RoadmapEntry(101, check_roadmap_freshness.READY_FOR_CONTRIBUTORS)
        ]
        states = {101: {"state": "CLOSED", "assignees": []}}
        problems = check_roadmap_freshness.check_entries(entries, states)
        self.assertEqual(len(problems), 1)
        self.assertIn("#101", problems[0])
        self.assertIn("CLOSED", problems[0])

    def test_ready_for_contributors_assigned_is_flagged(self) -> None:
        entries = [
            check_roadmap_freshness.RoadmapEntry(101, check_roadmap_freshness.READY_FOR_CONTRIBUTORS)
        ]
        states = {101: {"state": "OPEN", "assignees": [{"login": "octocat"}]}}
        problems = check_roadmap_freshness.check_entries(entries, states)
        self.assertEqual(len(problems), 1)
        self.assertIn("octocat", problems[0])

    def test_completed_open_is_flagged(self) -> None:
        entries = [check_roadmap_freshness.RoadmapEntry(301, check_roadmap_freshness.COMPLETED)]
        states = {301: {"state": "OPEN", "assignees": []}}
        problems = check_roadmap_freshness.check_entries(entries, states)
        self.assertEqual(len(problems), 1)
        self.assertIn("shipped", problems[0])

    def test_completed_closed_passes(self) -> None:
        entries = [check_roadmap_freshness.RoadmapEntry(301, check_roadmap_freshness.COMPLETED)]
        states = {301: {"state": "CLOSED", "assignees": []}}
        self.assertEqual(check_roadmap_freshness.check_entries(entries, states), [])

    def test_near_term_closed_is_flagged(self) -> None:
        entries = [check_roadmap_freshness.RoadmapEntry(201, check_roadmap_freshness.NEAR_TERM)]
        states = {201: {"state": "CLOSED", "assignees": []}}
        problems = check_roadmap_freshness.check_entries(entries, states)
        self.assertEqual(len(problems), 1)
        self.assertIn("promoting", problems[0])

    def test_near_term_open_passes(self) -> None:
        entries = [check_roadmap_freshness.RoadmapEntry(201, check_roadmap_freshness.NEAR_TERM)]
        states = {201: {"state": "OPEN", "assignees": []}}
        self.assertEqual(check_roadmap_freshness.check_entries(entries, states), [])

    def test_unresolvable_issue_is_flagged(self) -> None:
        entries = [
            check_roadmap_freshness.RoadmapEntry(999, check_roadmap_freshness.READY_FOR_CONTRIBUTORS)
        ]
        problems = check_roadmap_freshness.check_entries(entries, {})
        self.assertEqual(len(problems), 1)
        self.assertIn("could not resolve", problems[0])


class CheckStableVersionTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.pyproject_path = Path(self._tmpdir.name) / "pyproject.toml"

    def _write_pyproject(self, version: str) -> None:
        self.pyproject_path.write_text(
            f'[project]\nname = "example"\nversion = "{version}"\n',
            encoding="utf-8",
        )

    def test_matching_major_minor_passes(self) -> None:
        self._write_pyproject("0.9.1")
        self.assertIsNone(
            check_roadmap_freshness.check_stable_version(SAMPLE_ROADMAP, self.pyproject_path)
        )

    def test_mismatched_minor_is_flagged(self) -> None:
        self._write_pyproject("0.11.0")
        problem = check_roadmap_freshness.check_stable_version(SAMPLE_ROADMAP, self.pyproject_path)
        self.assertIsNotNone(problem)
        self.assertIn("v0.9.0", problem)
        self.assertIn("0.11.0", problem)

    def test_no_claimed_version_passes(self) -> None:
        self._write_pyproject("0.9.1")
        self.assertIsNone(
            check_roadmap_freshness.check_stable_version("# no version here", self.pyproject_path)
        )


class RunCheckTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.roadmap_path = Path(self._tmpdir.name) / "roadmap.md"
        self.pyproject_path = Path(self._tmpdir.name) / "pyproject.toml"
        self.pyproject_path.write_text(
            '[project]\nname = "example"\nversion = "0.9.1"\n', encoding="utf-8"
        )

    def test_fresh_roadmap_reports_no_problems(self) -> None:
        self.roadmap_path.write_text(SAMPLE_ROADMAP, encoding="utf-8")

        def fake_fetch(repo: str, numbers: list[int]) -> dict[int, dict[str, object]]:
            del repo
            return {
                number: {"state": "OPEN" if number != 301 and number not in (302, 303) else "CLOSED", "assignees": []}
                for number in numbers
            }

        problems = check_roadmap_freshness.run_check(
            self.roadmap_path, self.pyproject_path, "example/repo", fetch_issue_states=fake_fetch
        )
        self.assertEqual(problems, [])

    def test_stale_roadmap_reports_problems(self) -> None:
        self.roadmap_path.write_text(SAMPLE_ROADMAP, encoding="utf-8")

        def fake_fetch(repo: str, numbers: list[int]) -> dict[int, dict[str, object]]:
            del repo
            # Everything reported closed: flags the two "Ready" issues, and
            # passes for "Completed" (already expected closed).
            return {number: {"state": "CLOSED", "assignees": []} for number in numbers}

        problems = check_roadmap_freshness.run_check(
            self.roadmap_path, self.pyproject_path, "example/repo", fetch_issue_states=fake_fetch
        )
        self.assertTrue(any("#101" in p for p in problems))
        self.assertTrue(any("#102" in p for p in problems))
        self.assertTrue(any("#201" in p for p in problems))
        self.assertTrue(any("#202" in p for p in problems))


if __name__ == "__main__":
    unittest.main()
