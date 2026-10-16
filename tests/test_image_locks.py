import importlib.util
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "check_image_locks", REPO_ROOT / "scripts" / "check_image_locks.py"
)
check_image_locks = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_image_locks)

DOCKERFILES = {
    "dlt": "images/dlt/Dockerfile",
    "dbt": "images/dbt/Dockerfile",
    "dagster-base": "images/dagster/base/Dockerfile",
    "dagster-hardened": "images/dagster/hardened/Dockerfile",
    "superset": "images/superset/base/Dockerfile",
}


class ImageLockTest(unittest.TestCase):
    def test_dockerfiles_install_requirements_with_hashes(self) -> None:
        for name, path in DOCKERFILES.items():
            with self.subTest(image=name):
                text = (REPO_ROOT / path).read_text(encoding="utf-8")
                installs = re.findall(r"(?:pip install|uv pip install)(?:\\\n|[^\n])*", text)
                requirement_installs = [i for i in installs if "requirements" in i]
                self.assertTrue(requirement_installs)
                for install in requirement_installs:
                    self.assertIn("--require-hashes", install)

    def test_every_lock_has_header_and_hashes_and_a_source_file(self) -> None:
        locks = sorted((REPO_ROOT / "images").rglob("requirements*.txt"))
        self.assertGreaterEqual(len(locks), 5)
        for lock in locks:
            with self.subTest(lock=str(lock.relative_to(REPO_ROOT))):
                command = check_image_locks.compile_command(lock)
                self.assertIsNotNone(command)
                self.assertIn("--generate-hashes", command)
                self.assertTrue(lock.with_suffix(".in").is_file())
                self.assertIn("--hash=sha256:", lock.read_text(encoding="utf-8"))

    def test_ci_audits_the_locks_and_checks_for_drift(self) -> None:
        ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("scripts/check_image_locks.py", ci)
        self.assertIn("git diff --exit-code images", ci)
        self.assertIn("pip-audit --require-hashes --disable-pip", ci)


if __name__ == "__main__":
    unittest.main()
