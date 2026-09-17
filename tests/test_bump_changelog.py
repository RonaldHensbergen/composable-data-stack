import contextlib
import datetime
import importlib.util
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "bump_changelog.py"

_spec = importlib.util.spec_from_file_location("bump_changelog", SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
bump_changelog_module = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = bump_changelog_module
_spec.loader.exec_module(bump_changelog_module)


class BumpChangelogTest(unittest.TestCase):
    def test_dates_unreleased_section_and_reinserts_empty_one(self) -> None:
        text = (
            "# Changelog\n\n"
            "## [Unreleased]\n\n"
            "### Added\n\n"
            "- New thing (#1).\n\n"
            "## [0.8.0] - 2026-09-04\n\n"
            "### Added\n\n"
            "- Old thing (#2).\n"
        )

        updated = bump_changelog_module.bump_changelog(
            text, "0.9.0", release_date=datetime.date(2026, 9, 11)
        )

        self.assertIn("## [Unreleased]\n\n## [0.9.0] - 2026-09-11", updated)
        # The previously unreleased content now lives under the dated heading.
        self.assertIn(
            "## [0.9.0] - 2026-09-11\n\n### Added\n\n- New thing (#1).", updated
        )
        # Older, already-dated sections are untouched.
        self.assertIn("## [0.8.0] - 2026-09-04", updated)
        # Exactly one `[Unreleased]` heading remains (the fresh empty one).
        self.assertEqual(updated.count("## [Unreleased]"), 1)

    def test_missing_unreleased_heading_raises(self) -> None:
        text = "# Changelog\n\n## [0.8.0] - 2026-09-04\n\n- Old thing.\n"

        with self.assertRaises(ValueError) as ctx:
            bump_changelog_module.bump_changelog(
                text, "0.9.0", release_date=datetime.date(2026, 9, 11)
            )
        self.assertIn("Unreleased", str(ctx.exception))

    def test_duplicate_version_heading_raises(self) -> None:
        text = (
            "# Changelog\n\n"
            "## [Unreleased]\n\n"
            "## [0.9.0] - 2026-09-01\n\n"
            "- Already dated.\n"
        )

        with self.assertRaises(ValueError) as ctx:
            bump_changelog_module.bump_changelog(
                text, "0.9.0", release_date=datetime.date(2026, 9, 11)
            )
        self.assertIn("already has", str(ctx.exception))

    def test_main_updates_changelog_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            changelog_path = Path(tmpdir) / "CHANGELOG.md"
            changelog_path.write_text(
                "# Changelog\n\n## [Unreleased]\n\n- Something (#1).\n",
                encoding="utf-8",
            )

            # main() resolves CHANGELOG.md relative to the script's own repo
            # root, so exercise it as a subprocess with CHANGELOG.md copied
            # alongside a throwaway copy of the script instead.
            script_copy_dir = Path(tmpdir) / "scripts"
            script_copy_dir.mkdir()
            script_copy = script_copy_dir / "bump_changelog.py"
            script_copy.write_text(SCRIPT_PATH.read_text(encoding="utf-8"), encoding="utf-8")

            result = subprocess.run(
                [sys.executable, str(script_copy), "1.2.3"],
                cwd=tmpdir,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            updated = changelog_path.read_text(encoding="utf-8")
            self.assertIn("## [Unreleased]\n\n## [1.2.3] -", updated)

    def test_main_reports_usage_error(self) -> None:
        stderr = io.StringIO()
        # main() reads sys.argv directly, so patch it instead of passing args.
        old_argv = sys.argv
        try:
            sys.argv = ["bump_changelog.py"]
            with contextlib.redirect_stderr(stderr):
                exit_code = bump_changelog_module.main()
        finally:
            sys.argv = old_argv
        self.assertEqual(exit_code, 2)
        self.assertIn("Usage", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
