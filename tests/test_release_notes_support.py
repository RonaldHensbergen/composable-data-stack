import importlib.util
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_module(name: str, script_path: Path):
    spec = importlib.util.spec_from_file_location(name, script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


render_support_notice = _load_module(
    "render_support_notice", REPO_ROOT / "scripts" / "render_support_notice.py"
)
check_release_notes_support = _load_module(
    "check_release_notes_support",
    REPO_ROOT / "scripts" / "check_release_notes_support.py",
)


class RenderSupportNoticeTest(unittest.TestCase):
    def test_render_includes_heading_and_version(self) -> None:
        notice = render_support_notice.render("v1.2.3")
        self.assertIn("## Support", notice)
        self.assertIn("`1.2.3`", notice)

    def test_render_includes_policy_link_and_grace_period(self) -> None:
        notice = render_support_notice.render("v0.9.1")
        self.assertIn("docs/security-support-policy.md", notice)
        self.assertIn("14-day", notice)

    def test_render_strips_leading_v_from_tag(self) -> None:
        notice = render_support_notice.render("v2.0.0")
        self.assertNotIn("`v2.0.0`", notice)
        self.assertIn("`2.0.0`", notice)


class CheckReleaseNotesSupportTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.notes_path = Path(self._tmpdir.name) / "release_notes.md"

    def test_missing_file_fails(self) -> None:
        error = check_release_notes_support.check(self.notes_path)
        self.assertIsNotNone(error)
        self.assertIn("not found", error)

    def test_notes_without_support_section_fail(self) -> None:
        self.notes_path.write_text("### Fixed\n\n- Something (#1)\n", encoding="utf-8")
        error = check_release_notes_support.check(self.notes_path)
        self.assertIsNotNone(error)
        self.assertIn("missing a '## Support' section", error)

    def test_notes_with_support_section_pass(self) -> None:
        self.notes_path.write_text(
            "### Fixed\n\n- Something (#1)\n\n## Support\n\nSupported.\n",
            encoding="utf-8",
        )
        error = check_release_notes_support.check(self.notes_path)
        self.assertIsNone(error)

    def test_rendered_notice_passes_the_check(self) -> None:
        self.notes_path.write_text(
            render_support_notice.render("v1.0.0"), encoding="utf-8"
        )
        error = check_release_notes_support.check(self.notes_path)
        self.assertIsNone(error)


if __name__ == "__main__":
    unittest.main()
