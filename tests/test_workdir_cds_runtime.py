"""Tests for the cds-runtime workdir migration (issue #987).

Covers the shared example workload (workdirs/db_roundtrip.py) and the
dagster workdir's cds-runtime-based postgres_connection module, both
against SQLite through cds-runtime's env mapping — the same
application code that runs in the dagster and dlt containers.
"""
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RUNTIME_ROOT = REPO_ROOT / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

SQLALCHEMY_AVAILABLE = True
try:
    import sqlalchemy  # noqa: F401
except ModuleNotFoundError:
    SQLALCHEMY_AVAILABLE = False


def _load_from_path(name: str, file_path: Path):
    spec = importlib.util.spec_from_file_location(name, file_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@unittest.skipIf(not SQLALCHEMY_AVAILABLE, "SQLAlchemy is not installed in this environment")
class DbRoundtripExampleTest(unittest.TestCase):
    """The shared workload runs unchanged wherever a named connection exists."""

    def _roundtrip(self, marker: str) -> str:
        module = _load_from_path("cds_db_roundtrip_test", REPO_ROOT / "workdirs" / "db_roundtrip.py")
        return module.roundtrip("example", marker)

    def test_roundtrip_writes_and_reads_back(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            import os

            old = os.environ.get("CDS_DB_EXAMPLE_SQLITE_PATH")
            os.environ["CDS_DB_EXAMPLE_SQLITE_PATH"] = str(Path(tmpdir) / "roundtrip.db")
            try:
                marker = "hello-from-the-example-workload"
                self.assertEqual(self._roundtrip(marker), marker)
                # A second call reads the same row through a fresh connection.
                self.assertEqual(self._roundtrip("second"), "second")
            finally:
                if old is None:
                    os.environ.pop("CDS_DB_EXAMPLE_SQLITE_PATH", None)
                else:
                    os.environ["CDS_DB_EXAMPLE_SQLITE_PATH"] = old

    def test_roundtrip_resolves_connection_uri_when_configured(self):
        from cds_runtime import connections

        db = connections.database(
            "example",
            env={"CDS_DB_EXAMPLE_CONNECTION_URI": "sqlite:///:memory:"},
        )
        self.assertEqual(
            db.connection_uri,
            "sqlite:///:memory:",
        )


@unittest.skipIf(not SQLALCHEMY_AVAILABLE, "SQLAlchemy is not installed in this environment")
class DagsterPostgresConnectionMigrationTest(unittest.TestCase):
    """The dagster workdir's cds-runtime module replaces the legacy helper."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.module = _load_from_path(
            "cds_postgres_connection_test",
            REPO_ROOT / "workdirs" / "dagster" / "postgres_connection.py",
        )
        self.env = {
            "CDS_ANALYTICS_DB_BACKEND": "sqlite",
            "CDS_ANALYTICS_DB_SQLITE_PATH": str(Path(self._tmp.name) / "events.db"),
            "CDS_LOCAL_EVENT_LOG_PATH": str(Path(self._tmp.name) / "events.jsonl"),
        }

    def test_analytics_connection_adopts_the_legacy_env_block(self):
        import os

        saved = {key: os.environ.get(key) for key in self.env}
        os.environ.update(self.env)
        try:
            db = self.module.connections.database("analytics", prefix="CDS_ANALYTICS_DB", env=self.env)
            self.assertTrue(db.connection_uri.startswith("sqlite:///"))
            self.assertEqual(self.module.get_postgres_connection_uri(), db.connection_uri)
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_insert_incoming_file_event_persists_and_snapshots(self):
        payload = {"event": "test-event", "file_name": "x.csv", "row_count": 3}
        self.module.insert_incoming_file_event(payload, "cds_ingestion", env=self.env)
        db = self.module.connections.database("analytics", prefix="CDS_ANALYTICS_DB", env=self.env)
        rows = db.fetch_all("SELECT event_type, asset_key FROM incoming_file_events")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["event_type"], "test-event")
        self.assertEqual(rows[0]["asset_key"], "cds_ingestion")
        snapshot = Path(self.env["CDS_LOCAL_EVENT_LOG_PATH"]).read_text(encoding="utf-8")
        self.assertIn("test-event", snapshot)

    def test_insert_incoming_file_event_without_configuration_only_snapshots(self):
        payload = {"event": "no-db", "file_name": "y.csv"}
        self.module.insert_incoming_file_event(payload, "cds_ingestion", env={
            **self.env,
            "CDS_ANALYTICS_DB_SQLITE_PATH": "",
            "CDS_ANALYTICS_DB_BACKEND": "",
        })
        self.assertTrue(Path(self.env["CDS_LOCAL_EVENT_LOG_PATH"]).exists())


if __name__ == "__main__":
    unittest.main()
