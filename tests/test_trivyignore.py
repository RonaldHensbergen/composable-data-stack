import datetime
import re
import unittest
from pathlib import Path

_CVE = re.compile(r"^(?:CVE-\d{4}-\d{4,}|AVD-\d{4}-\d{4,})$", re.IGNORECASE)
_ENTRY = re.compile(
    r"^(?:CVE-\d{4}-\d{4,}|AVD-\d{4}-\d{4,})\s+exp:(\d{4}-\d{2}-\d{2})$",
    re.IGNORECASE,
)


class TrivyIgnoreTest(unittest.TestCase):
    """Enforces the exception contract for container-scan findings.

    See docs/image-scanning.md. Entries use trivy's native ignore format
    (``<id> exp:YYYY-MM-DD``) and are preceded by a comment block naming
    the remediation owner and justification.
    """

    def setUp(self) -> None:
        repo_root = Path(__file__).resolve().parent.parent
        self.path = repo_root / ".trivyignore"
        self.lines = self.path.read_text(encoding="utf-8").splitlines()

    def _entries(self):
        """Yield (entry_line, comment_block) for each non-comment line."""
        comments: list[str] = []
        for line in self.lines:
            stripped = line.strip()
            if not stripped:
                comments = []
                continue
            if stripped.startswith("#"):
                comments.append(stripped)
                continue
            yield stripped, list(comments)
            comments = []

    def test_fixture_exists(self) -> None:
        self.assertTrue(self.path.is_file(), ".trivyignore must exist")

    def test_entries_use_native_trivy_syntax(self) -> None:
        for line, _ in self._entries():
            with self.subTest(line=line):
                self.assertRegex(
                    line,
                    _ENTRY.pattern,
                    "entry must be '<id> exp:YYYY-MM-DD' (trivy native format)",
                )

    def test_entries_have_unique_ids(self) -> None:
        seen = set()
        for line, _ in self._entries():
            first = line.split(None, 1)[0]
            self.assertRegex(
                first, _CVE.pattern, "entry must start with a vulnerability id"
            )
            self.assertNotIn(first, seen, "duplicate vulnerability id")
            seen.add(first)

    def test_expiry_is_valid_and_within_90_days(self) -> None:
        for line, _ in self._entries():
            with self.subTest(line=line):
                match = re.match(r"^.*exp:(\d{4}-\d{2}-\d{2})$", line)
                self.assertIsNotNone(match, "entry must end with exp:YYYY-MM-DD")
                exp_date = datetime.date.fromisoformat(match.group(1))
                today = datetime.date.today()
                self.assertLessEqual(today, exp_date, "expiry must not be in the past")
                max_exp = today + datetime.timedelta(days=90)
                self.assertLessEqual(exp_date, max_exp, "expiry may not exceed 90 days")

    def test_comment_block_above_entry_names_owner_and_justification(self) -> None:
        for line, comments in self._entries():
            with self.subTest(line=line):
                block = "\n".join(comments)
                self.assertRegex(
                    block,
                    r"owner:@[A-Za-z0-9-]+",
                    "comment block above the entry must have owner:@<handle>",
                )
                self.assertRegex(
                    block,
                    r"#\s*.+",
                    "comment block above the entry must have a justification",
                )


if __name__ == "__main__":
    unittest.main()
