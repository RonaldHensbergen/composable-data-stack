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
    venv_cds = _REPO_ROOT / ".venv" / "bin" / "cds"
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


if __name__ == "__main__":
    unittest.main()