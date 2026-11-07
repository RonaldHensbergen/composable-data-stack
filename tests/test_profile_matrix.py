"""Every shipped profile must validate and render on every PR (issue #1208).

Wraps scripts/check_profile_matrix.py (the cheap, Docker-free tier of the
profile-matrix E2E): for every profiles/*/profile.yaml and every environment
overlay the script runs the same code paths as `cds validate` and
`cds render` -- validate_profile, build_plan, render_compose, and
render_helm where the target is defined -- and fails on any error-level
diagnostic. The dedicated `profile-matrix` job in .github/workflows/ci.yml
runs the script directly; this test keeps the matrix enforced by `make test`
on all CI operating systems as well.
"""

import importlib.util
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_profile_matrix.py"

_spec = importlib.util.spec_from_file_location("check_profile_matrix", SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
check_profile_matrix = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = check_profile_matrix
_spec.loader.exec_module(check_profile_matrix)


class ProfileMatrixTest(unittest.TestCase):
    def test_every_shipped_profile_validates_and_renders(self) -> None:
        with TemporaryDirectory(prefix="cds-profile-matrix-test-") as workdir:
            failures = check_profile_matrix.check_matrix(
                check_profile_matrix.PROFILES_ROOT, Path(workdir), verbose=False
            )
        self.assertEqual([], failures)


if __name__ == "__main__":
    unittest.main()
