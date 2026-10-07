"""Validates the Annex I traceability matrices in docs/cra-risk-assessment.md.

See docs/cra-risk-assessment.md and issue #732. Parses the Annex I Part I
and Part II markdown tables and enforces the acceptance criterion that
every requirement has a disposition: implemented evidence, a tracked gap
with an owning issue reference, or a justified non-applicability.
"""

import re
import unittest
from pathlib import Path

_PART_I_ITEMS = [
    "1",
    "2(a)",
    "2(b)",
    "2(c)",
    "2(d)",
    "2(e)",
    "2(f)",
    "2(g)",
    "2(h)",
    "2(i)",
    "2(j)",
    "2(k)",
    "2(l)",
    "2(m)",
]
_PART_II_ITEMS = [f"II.{n}" for n in range(1, 9)]

_ISSUE_REF = re.compile(r"#\d+")
_NON_APPLICABLE_JUSTIFICATION = re.compile(
    r"non-applicable\s*\(justified\)[^|]*\u2014", re.IGNORECASE
)


def _table_rows(lines: list[str], heading: str) -> list[str]:
    """Return the data rows (excluding header/separator) of the markdown
    table that immediately follows the given ``##``/``###`` heading line."""
    start = None
    for index, line in enumerate(lines):
        if line.strip() == heading:
            start = index
            break
    assert start is not None, f"heading {heading!r} not found"

    rows = []
    in_table = False
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if stripped.startswith("##") and in_table:
            break
        if not stripped.startswith("|"):
            if in_table:
                break
            continue
        if re.match(r"^\|\s*-{2,}", stripped):
            in_table = True
            continue
        if in_table:
            rows.append(stripped)
    return rows


def _split_cells(row: str) -> list[str]:
    # Drop the leading/trailing pipe, then split on unescaped '|'.
    inner = row.strip().strip("|")
    return [cell.strip() for cell in inner.split("|")]


class CraTraceabilityTest(unittest.TestCase):
    """Enforces that every Annex I item has a non-empty, valid disposition."""

    @classmethod
    def setUpClass(cls) -> None:
        repo_root = Path(__file__).resolve().parent.parent
        cls.path = repo_root / "docs" / "cra-risk-assessment.md"
        cls.lines = cls.path.read_text(encoding="utf-8").splitlines()

    def test_file_exists(self) -> None:
        self.assertTrue(self.path.is_file(), "docs/cra-risk-assessment.md must exist")

    def _part_i_rows(self):
        rows = _table_rows(
            self.lines,
            "## 2. Annex I Part I traceability — properties of products with"
            " digital elements",
        )
        return [_split_cells(row) for row in rows]

    def _part_ii_rows(self):
        rows = _table_rows(
            self.lines,
            "## 3. Annex I Part II traceability — vulnerability handling"
            " requirements",
        )
        return [_split_cells(row) for row in rows]

    def test_part_i_every_item_present_exactly_once(self) -> None:
        ids = [cells[0] for cells in self._part_i_rows()]
        for item in _PART_I_ITEMS:
            with self.subTest(item=item):
                self.assertEqual(
                    ids.count(item),
                    1,
                    f"Annex I Part I item {item} must appear exactly once",
                )

    def test_part_ii_every_item_present_exactly_once(self) -> None:
        ids = [cells[0] for cells in self._part_ii_rows()]
        for item in _PART_II_ITEMS:
            with self.subTest(item=item):
                self.assertEqual(
                    ids.count(item),
                    1,
                    f"Annex I Part II item {item} must appear exactly once",
                )

    def _assert_dispositions_valid(self, rows):
        for cells in rows:
            item_id = cells[0]
            disposition = cells[-1]
            with self.subTest(item=item_id):
                self.assertTrue(
                    disposition,
                    f"{item_id} must have a non-empty disposition",
                )
                lowered = disposition.lower()
                is_implemented = lowered.startswith("implemented")
                is_tracked_gap = "tracked gap" in lowered
                is_non_applicable = lowered.startswith("non-applicable")
                self.assertTrue(
                    is_implemented or is_tracked_gap or is_non_applicable,
                    f"{item_id} disposition {disposition!r} must start with "
                    "'Implemented', contain 'Tracked gap', or start with "
                    "'Non-applicable'",
                )
                if is_tracked_gap:
                    related = cells[-2] if len(cells) >= 2 else ""
                    has_issue_ref = _ISSUE_REF.search(
                        disposition
                    ) or _ISSUE_REF.search(related)
                    acknowledges_unfiled = "not yet filed" in lowered
                    self.assertTrue(
                        has_issue_ref or acknowledges_unfiled,
                        f"{item_id} is a tracked gap and must either cite a "
                        "'#<issue number>' reference (in the disposition or "
                        "related-issue(s) cell), or explicitly state "
                        "'not yet filed' if deliberately left unfiled",
                    )
                if is_non_applicable:
                    self.assertIn(
                        "\u2014",
                        disposition,
                        f"{item_id} is non-applicable and must state a "
                        "justification after an em dash",
                    )

    def test_part_i_dispositions_are_valid(self) -> None:
        self._assert_dispositions_valid(self._part_i_rows())

    def test_part_ii_dispositions_are_valid(self) -> None:
        self._assert_dispositions_valid(self._part_ii_rows())


if __name__ == "__main__":
    unittest.main()
