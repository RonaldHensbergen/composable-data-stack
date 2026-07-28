import importlib.util
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_release_version.py"

_spec = importlib.util.spec_from_file_location("check_release_version", SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
check_release_version = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = check_release_version
_spec.loader.exec_module(check_release_version)


class ReleaseVersionCheckTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.pyproject_path = Path(self._tmpdir.name) / "pyproject.toml"

    def _write_pyproject(self, version: str) -> None:
        self.pyproject_path.write_text(
            f'[project]\nname = "example"\nversion = "{version}"\n',
            encoding="utf-8",
        )

    def test_matching_tag_and_version_pass(self) -> None:
        self._write_pyproject("0.2.0a0")
        error = check_release_version.check("v0.2.0-alpha", self.pyproject_path)
        self.assertIsNone(error)

    def test_matching_stable_tag_and_version_pass(self) -> None:
        self._write_pyproject("1.4.2")
        error = check_release_version.check("v1.4.2", self.pyproject_path)
        self.assertIsNone(error)

    def test_mismatched_version_fails(self) -> None:
        self._write_pyproject("0.1.1")
        error = check_release_version.check("v0.2.0-alpha", self.pyproject_path)
        self.assertIsNotNone(error)
        self.assertIn("does not match", error)

    def test_invalid_tag_fails(self) -> None:
        self._write_pyproject("0.1.1")
        error = check_release_version.check("vnot-a-version", self.pyproject_path)
        self.assertIsNotNone(error)
        self.assertIn("not a valid version", error)

    def test_invalid_pyproject_version_fails(self) -> None:
        self._write_pyproject("not-a-version")
        error = check_release_version.check("v0.1.1", self.pyproject_path)
        self.assertIsNotNone(error)
        self.assertIn("not a valid PEP 440 version", error)

    def test_current_pyproject_matches_latest_git_tag_shape(self) -> None:
        # Guards against the exact real-world drift this script was written for:
        # pyproject.toml declaring a version PEP-440-equivalent to `v<version>` tags.
        error = check_release_version.check(
            "v" + check_release_version.pyproject_version(REPO_ROOT / "pyproject.toml"),
            REPO_ROOT / "pyproject.toml",
        )
        self.assertIsNone(error)


if __name__ == "__main__":
    unittest.main()
