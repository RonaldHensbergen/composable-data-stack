import importlib.util
import os
import tempfile
import unittest
import unittest.mock
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DAGSTER_IMG = _REPO_ROOT / "images" / "dagster"

# Import generate_config directly from its file path (not a package)
_spec = importlib.util.spec_from_file_location(
    "generate_config", _DAGSTER_IMG / "generate_config.py"
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


class TestDetectBackend(unittest.TestCase):
    def test_postgresql_uri(self):
        self.assertEqual(_mod.detect_backend("postgresql://user:pass@host:5432/db"), "postgres")

    def test_mysql_uri(self):
        self.assertEqual(_mod.detect_backend("mysql+pymysql://user:pass@host:3306/db"), "mysql")

    def test_sqlite_uri(self):
        self.assertEqual(_mod.detect_backend("sqlite:////tmp/dagster.db"), "sqlite")

    def test_empty_uri_defaults_to_postgres(self):
        self.assertEqual(_mod.detect_backend(""), "postgres")

    def test_unknown_uri_defaults_to_postgres(self):
        self.assertEqual(_mod.detect_backend("unknown://something"), "postgres")


class TestGenerateDagsterYaml(unittest.TestCase):
    def _generate(self, env: dict) -> str:
        """Run generate_dagster_yaml with the given env vars and return the output YAML."""
        with tempfile.TemporaryDirectory() as tmpdir:
            merged = {**env, "DAGSTER_HOME": tmpdir}
            with unittest.mock.patch.dict(os.environ, merged, clear=False):
                os.environ.pop("DB_BACKEND", None)
                _mod.generate_dagster_yaml()
            return (Path(tmpdir) / "dagster.yaml").read_text()

    def test_postgres_backend_via_connection_uri(self):
        output = self._generate(
            {"DAGSTER_DB_CONNECTION_URI": "postgresql://analytics:pass@postgres:5432/dagster"}
        )
        self.assertIn("dagster_postgres.run_storage", output)
        self.assertIn("PostgresRunStorage", output)
        self.assertIn("dagster_postgres.schedule_storage", output)
        self.assertIn("PostgresScheduleStorage", output)
        self.assertIn("dagster_postgres.event_log", output)
        self.assertIn("PostgresEventLogStorage", output)

    def test_postgres_config_uses_generic_env_vars(self):
        output = self._generate(
            {"DAGSTER_DB_CONNECTION_URI": "postgresql://analytics:pass@postgres:5432/dagster"}
        )
        self.assertIn("DAGSTER_DB_HOST", output)
        self.assertIn("DAGSTER_DB_USER", output)
        self.assertIn("DAGSTER_DB_PASSWORD", output)
        self.assertIn("DAGSTER_DB_NAME", output)
        self.assertIn("DAGSTER_DB_PORT", output)

    def test_postgres_config_has_no_legacy_postgres_vars(self):
        output = self._generate(
            {"DAGSTER_DB_CONNECTION_URI": "postgresql://analytics:pass@postgres:5432/dagster"}
        )
        self.assertNotIn("DAGSTER_POSTGRES_HOST", output)
        self.assertNotIn("DAGSTER_POSTGRES_USER", output)
        self.assertNotIn("DAGSTER_POSTGRES_PASSWORD", output)
        self.assertNotIn("DAGSTER_POSTGRES_DB", output)

    def test_sqlite_backend_via_connection_uri(self):
        output = self._generate({"DAGSTER_DB_CONNECTION_URI": "sqlite:////tmp/dagster.db"})
        self.assertIn("SqliteRunStorage", output)
        self.assertIn("SqliteScheduleStorage", output)
        self.assertIn("SqliteEventLogStorage", output)
        self.assertIn("DAGSTER_SQLITE_DIR", output)
        self.assertNotIn("dagster_postgres", output)

    def test_db_backend_env_overrides_uri_detection(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {
                "DB_BACKEND": "sqlite",
                "DAGSTER_DB_CONNECTION_URI": "postgresql://host/db",
                "DAGSTER_HOME": tmpdir,
            }
            with unittest.mock.patch.dict(os.environ, env, clear=False):
                _mod.generate_dagster_yaml()
            output = (Path(tmpdir) / "dagster.yaml").read_text()
        self.assertIn("SqliteRunStorage", output)
        self.assertNotIn("dagster_postgres", output)

    def test_output_written_to_dagster_home(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {
                "DAGSTER_DB_CONNECTION_URI": "postgresql://host/db",
                "DAGSTER_HOME": tmpdir,
            }
            with unittest.mock.patch.dict(os.environ, env, clear=False):
                os.environ.pop("DB_BACKEND", None)
                _mod.generate_dagster_yaml()
            self.assertTrue((Path(tmpdir) / "dagster.yaml").exists())


if __name__ == "__main__":
    unittest.main()
