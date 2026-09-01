import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PROFILE_NAME = "local-dagster-postgres-superset"
_PROFILE_FILE = str(_REPO_ROOT / "profiles" / _PROFILE_NAME / "profile.yaml")
_PROFILE_ENV_FILE = _REPO_ROOT / ".env"


def _find_cds() -> list[str]:
    """Return the command list to invoke the cds CLI.

    Prefers the venv binary when it exists so the test works even when
    cds is not on the system PATH.
    """
    # Try to find cds in virtual environment (cross-platform)
    venv_dir = _REPO_ROOT / ".venv"
    
    # On Windows: .venv\Scripts\cds.exe
    if sys.platform == "win32":
        venv_cds = venv_dir / "Scripts" / "cds.exe"
    else:
        # On Unix-like systems: .venv/bin/cds
        venv_cds = venv_dir / "bin" / "cds"
    
    if venv_cds.exists():
        return [str(venv_cds)]
    return [sys.executable, "-m", "cli.main"]


class TestCDSWorkflow(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.cds = _find_cds()
        cls.repo_root = _REPO_ROOT

        env_file = _PROFILE_ENV_FILE
        cls._created_env = False
        if not env_file.exists():
            init_result = subprocess.run(
                cls.cds + ["init", _PROFILE_NAME],
                cwd=str(cls.repo_root),
                capture_output=True,
                text=True,
            )
            if init_result.returncode != 0:
                raise RuntimeError(
                    "Failed to initialize .env for workflow tests:\n"
                    f"stdout: {init_result.stdout}\n"
                    f"stderr: {init_result.stderr}"
                )
            cls._created_env = True

        cls.render_tmpdir = tempfile.TemporaryDirectory()

    @classmethod
    def tearDownClass(cls):
        cls.render_tmpdir.cleanup()
        if cls._created_env:
            env_file = _PROFILE_ENV_FILE
            if env_file.exists():
                env_file.unlink()

    def _run(self, *args, extra_env: dict | None = None) -> subprocess.CompletedProcess:
        env = os.environ.copy()
        env.pop("CDS_PROFILE_PATH", None)
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            self.cds + list(args),
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
            env=env,
        )

    def test_commands(self):
        """Test validate, plan, and render — with and without an explicit profile."""
        # render needs an output path so it does not write into the repo root
        render_output = str(Path(self.render_tmpdir.name) / "docker-compose.yml")

        cases = [
            ("validate", [], []),
            ("validate", [_PROFILE_NAME], []),
            ("plan", [], []),
            ("plan", [_PROFILE_NAME], []),
            ("render", ["--output", render_output], []),
            ("render", [_PROFILE_NAME, "--output", render_output], []),
        ]

        # CDS_PROFILE_PATH must point to the specific profile file when no
        # profile name is passed on the command line, otherwise auto-discovery
        # fails when multiple profiles are present in the profiles/ directory.
        env_with_profile = {"CDS_PROFILE_PATH": _PROFILE_FILE}

        for cmd, extra_args, _ in cases:
            use_profile = bool(extra_args) and extra_args[0] == _PROFILE_NAME
            label = f"{cmd} {'with' if use_profile else 'without'} profile arg"

            with self.subTest(label=label):
                env = env_with_profile if not use_profile else None
                result = self._run(cmd, *extra_args, extra_env=env)
                self.assertEqual(
                    result.returncode, 0,
                    f"cds {cmd} {' '.join(extra_args)} failed:\n"
                    f"stdout: {result.stdout}\nstderr: {result.stderr}",
                )

    def test_test_command(self):
        """`cds test` bundles validate/security/plan/render into one exit code.

        This profile's committed secret values (from `cds init`) are the
        same kind of low-entropy, non-external-reference values CI supplies
        via env vars, so the security stage always reports HIGH findings
        (CDS-SEC-001/003) here -- the same reason CI's "Run end-to-end smoke
        test" step (see .github/workflows/ci.yml) asserts this specific
        per-stage outcome instead of requiring `cds test` to exit 0.
        """
        env_with_profile = {"CDS_PROFILE_PATH": _PROFILE_FILE}

        for extra_args, use_profile_arg in (([], False), ([_PROFILE_NAME], True)):
            label = f"test {'with' if use_profile_arg else 'without'} profile arg"
            with self.subTest(label=label):
                env = env_with_profile if not use_profile_arg else None
                result = self._run("test", *extra_args, extra_env=env)
                self.assertEqual(
                    result.returncode, 1,
                    f"cds test {' '.join(extra_args)} exited "
                    f"{result.returncode}, expected 1 (security stage is "
                    f"expected to fail against this profile's committed "
                    f"secret values):\nstdout: {result.stdout}\n"
                    f"stderr: {result.stderr}",
                )
                for stage, status in (
                    ("validate", "PASS"),
                    ("security", "FAIL"),
                    ("plan", "PASS"),
                    ("render", "PASS"),
                ):
                    self.assertIn(
                        f"[{status}] {stage}", result.stdout,
                        f"cds test {' '.join(extra_args)} did not report "
                        f"[{status}] {stage}:\nstdout: {result.stdout}",
                    )


class ProductionPlaintextExposureWorkflowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cds = _find_cds()
        cls.repo_root = _REPO_ROOT
        cls.fixture_root = (
            _REPO_ROOT / "tests" / "fixtures" / "security" / "plaintext-exposure"
        )
        cls.modules_root = cls.fixture_root / "modules"

    def _run_test(self, profile_dir: str) -> subprocess.CompletedProcess:
        env = os.environ.copy()
        env["CDS_MODULE_PATH"] = str(self.modules_root)
        profile_path = self.fixture_root / profile_dir / "profile.yaml"
        return subprocess.run(
            self.cds + ["test", str(profile_path)],
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
            env=env,
        )

    def test_production_plaintext_exposure_policy_end_to_end(self):
        with self.subTest(case="valid tls reverse proxy"):
            result = self._run_test("profile-with-tls")
            self.assertEqual(
                result.returncode, 0,
                f"expected pass with TLS reverse proxy:\nstdout: {result.stdout}\nstderr: {result.stderr}",
            )
            for stage in ("validate", "security", "plan", "render"):
                self.assertIn(f"[PASS] {stage}", result.stdout)
            self.assertNotIn("CDS-SEC-074", result.stdout)

        with self.subTest(case="missing tls reverse proxy"):
            result = self._run_test("profile-missing-tls")
            self.assertEqual(
                result.returncode, 1,
                f"expected failure without TLS reverse proxy:\nstdout: {result.stdout}\nstderr: {result.stderr}",
            )
            self.assertIn("[FAIL] security", result.stdout)
            self.assertIn("CDS-SEC-074", result.stdout)

        with self.subTest(case="waived plaintext exposure"):
            result = self._run_test("profile-waived-plaintext")
            self.assertEqual(
                result.returncode, 0,
                f"expected pass with explicit waiver:\nstdout: {result.stdout}\nstderr: {result.stderr}",
            )
            self.assertIn("[PASS] security", result.stdout)
            self.assertIn("W098", result.stderr)
            self.assertNotIn("CDS-SEC-074", result.stdout)


if __name__ == "__main__":
    unittest.main()