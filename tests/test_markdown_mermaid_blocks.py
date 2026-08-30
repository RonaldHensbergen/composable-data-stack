"""Regression test guarding against invalid Mermaid code fences.

A ```mermaid fenced code block must not start with a `---` line. Mermaid
interprets a leading `---` as the start of a YAML frontmatter directive
block, and a diagram body that isn't valid YAML frontmatter (or omits the
closing `---`) fails to render. This has slipped into documentation before
(see README.md), so this test scans all tracked Markdown files for the
pattern and fails if it recurs.
"""

import re
import subprocess
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Matches a ```mermaid fence whose very next non-fence line is a bare `---`.
_MERMAID_LEADING_FRONTMATTER_RE = re.compile(r"```mermaid\r?\n---\r?\n")


def _tracked_markdown_files():
    result = subprocess.run(
        ["git", "ls-files", "*.md"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [
        _REPO_ROOT / line
        for line in result.stdout.splitlines()
        if line.strip()
    ]


class MermaidCodeBlockTest(unittest.TestCase):
    def test_mermaid_fences_do_not_start_with_frontmatter_dashes(self):
        offending = []
        for path in _tracked_markdown_files():
            text = path.read_text(encoding="utf-8")
            if _MERMAID_LEADING_FRONTMATTER_RE.search(text):
                offending.append(str(path.relative_to(_REPO_ROOT)))

        self.assertEqual(
            offending,
            [],
            "Mermaid code blocks must not open with a bare '---' line "
            "immediately after the ```mermaid fence, as Mermaid treats it "
            "as an (invalid/unterminated) YAML frontmatter block: "
            f"{offending}",
        )


if __name__ == "__main__":
    unittest.main()
