import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path


class DagsterEntrypointBackendGuardTest(unittest.TestCase):
    """Exercises images/dagster/entrypoint.sh's backend-selection guard directly.

    The guard runs before entrypoint.sh touches any container-only paths (it
    exits or falls through before `cp /app/images/dagster/workspace.yaml ...`),
    so we can execute the real script text under `sh` with a marker appended
    to see whether the guard let execution continue.
    """

    MARKER = "GUARD_PASSED"

    @classmethod
    def setUpClass(cls) -> None:
        repo_root = Path(__file__).resolve().parent.parent
        entrypoint = (repo_root / "images" / "dagster" / "entrypoint.sh").read_text(encoding="utf-8")
        guard_snippet, _, _ = entrypoint.partition("cp /app/images/dagster/workspace.yaml")
        cls.guard_snippet = guard_snippet

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.dagster_home = self._tmpdir.name

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def run_guard(self, env: dict) -> subprocess.CompletedProcess:
        full_env = {"DAGSTER_HOME": self.dagster_home, **env}
        script = f"{self.guard_snippet}\necho {self.MARKER}\n"
        return subprocess.run(
            ["sh", "-c", script],
            env=full_env,
            capture_output=True,
            text=True,
            timeout=5,
        )

    def test_sqlite_backend_is_allowed_on_postgres_built_image(self) -> None:
        result = self.run_guard({"DB_BACKEND": "sqlite", "DAGSTER_IMAGE_DB_BACKEND": "postgres"})

        self.assertEqual(result.returncode, 0)
        self.assertIn(self.MARKER, result.stdout)

    def test_sqlite_backend_is_allowed_on_sqlite_built_image(self) -> None:
        result = self.run_guard({"DB_BACKEND": "sqlite", "DAGSTER_IMAGE_DB_BACKEND": "sqlite"})

        self.assertEqual(result.returncode, 0)
        self.assertIn(self.MARKER, result.stdout)

    def test_postgres_backend_is_allowed_on_postgres_built_image(self) -> None:
        result = self.run_guard({"DB_BACKEND": "postgres", "DAGSTER_IMAGE_DB_BACKEND": "postgres"})

        self.assertEqual(result.returncode, 0)
        self.assertIn(self.MARKER, result.stdout)

    def test_postgres_backend_is_rejected_on_sqlite_built_image(self) -> None:
        result = self.run_guard({"DB_BACKEND": "postgres", "DAGSTER_IMAGE_DB_BACKEND": "sqlite"})

        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(self.MARKER, result.stdout)
        self.assertIn("requires an image built with DB_BACKEND=postgres", result.stderr)

    def test_mysql_backend_is_rejected(self) -> None:
        result = self.run_guard({"DB_BACKEND": "mysql", "DAGSTER_IMAGE_DB_BACKEND": "postgres"})

        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(self.MARKER, result.stdout)
        self.assertIn("MySQL storage is not supported by this Dagster image", result.stderr)

    def test_unsupported_backend_is_rejected(self) -> None:
        result = self.run_guard({"DB_BACKEND": "bogus", "DAGSTER_IMAGE_DB_BACKEND": "postgres"})

        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(self.MARKER, result.stdout)
        self.assertIn("Unsupported Dagster database backend: bogus", result.stderr)

    def test_backend_is_inferred_from_connection_uri_when_unset(self) -> None:
        result = self.run_guard(
            {
                "DAGSTER_DB_CONNECTION_URI": "sqlite:////tmp/db.sqlite",
                "DAGSTER_IMAGE_DB_BACKEND": "postgres",
            }
        )

        self.assertEqual(result.returncode, 0)
        self.assertIn(self.MARKER, result.stdout)

    def test_sqlite_storage_dir_defaults_under_dagster_home(self) -> None:
        result = self.run_guard({"DB_BACKEND": "sqlite", "DAGSTER_IMAGE_DB_BACKEND": "postgres"})

        self.assertEqual(result.returncode, 0)
        expected_dir = Path(self.dagster_home) / "storage"
        self.assertTrue(expected_dir.is_dir())

    def test_sqlite_storage_dir_respects_explicit_override(self) -> None:
        with tempfile.TemporaryDirectory() as custom_dir:
            override_path = Path(custom_dir) / "custom-sqlite"
            result = self.run_guard(
                {
                    "DB_BACKEND": "sqlite",
                    "DAGSTER_IMAGE_DB_BACKEND": "postgres",
                    "DAGSTER_SQLITE_DIR": str(override_path),
                }
            )

            self.assertEqual(result.returncode, 0)
            self.assertTrue(override_path.is_dir())


class DagsterWorkspaceInstallTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        repo_root = Path(__file__).resolve().parent.parent
        entrypoint = (repo_root / "images" / "dagster" / "entrypoint.sh").read_text(encoding="utf-8")
        _, marker, remainder = entrypoint.partition('workspace_tmp="$DAGSTER_HOME/.workspace.yaml.tmp"')
        if not marker:
            raise AssertionError("entrypoint workspace installation block is missing")
        install_body, separator, _ = remainder.partition("python /app/images/dagster/generate_config.py")
        if not separator:
            raise AssertionError("entrypoint workspace installation block has no end marker")
        cls.install_snippet = marker + install_body

    def test_workspace_install_is_restart_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            dagster_home = Path(tmpdir) / "dagster-home"
            dagster_home.mkdir()
            source = Path(tmpdir) / "workspace.yaml"
            source.write_text("load_from: []\n", encoding="utf-8")
            source.chmod(0o444)
            script = self.install_snippet.replace(
                "/app/images/dagster/workspace.yaml",
                shlex.quote(str(source)),
            )

            first = subprocess.run(
                ["sh", "-eu", "-c", script],
                env={"DAGSTER_HOME": str(dagster_home)},
                capture_output=True,
                text=True,
                timeout=5,
            )
            second = subprocess.run(
                ["sh", "-eu", "-c", script],
                env={"DAGSTER_HOME": str(dagster_home)},
                capture_output=True,
                text=True,
                timeout=5,
            )

            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 0, second.stderr)
            destination = dagster_home / "workspace.yaml"
            self.assertEqual(destination.read_text(encoding="utf-8"), "load_from: []\n")
            self.assertTrue(destination.stat().st_mode & 0o200)


if __name__ == "__main__":
    unittest.main()
