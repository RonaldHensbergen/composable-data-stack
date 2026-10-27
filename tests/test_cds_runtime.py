"""Tests for the cds-runtime workload API (issue #219)."""
import os
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RUNTIME_ROOT = REPO_ROOT / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from cds_runtime import (  # noqa: E402
    DatabaseConnection,
    connections,
    env_prefix_for,
    redact_uri,
    redact_uris_in_text,
    resolve_connection_uri,
)
from cds_runtime.adapters import dagster  # noqa: E402
from cds_runtime.errors import (  # noqa: E402
    CdsRuntimeError,
    ConnectionSetupError,
    DriverUnavailableError,
    MissingConnectionSettingsError,
    QueryExecutionError,
    UnknownConnectionError,
)

try:
    import sqlalchemy  # noqa: F401

    SQLALCHEMY_AVAILABLE = True
except ModuleNotFoundError:
    SQLALCHEMY_AVAILABLE = False


class EnvPrefixTest(unittest.TestCase):
    def test_default_prefix_maps_name_to_env_namespace(self):
        self.assertEqual(env_prefix_for("analytics"), "CDS_DB_ANALYTICS")
        self.assertEqual(env_prefix_for("analytics-database"), "CDS_DB_ANALYTICS_DATABASE")

    def test_prefix_override_adopts_legacy_env_blocks(self):
        self.assertEqual(env_prefix_for("analytics", prefix="CDS_ANALYTICS_DB"), "CDS_ANALYTICS_DB")

    def test_invalid_names_are_rejected(self):
        for bad in ("", "has space", "drop;table", None, 3):
            with self.assertRaises(ValueError):
                env_prefix_for(bad)


class ConnectionUriResolutionTest(unittest.TestCase):
    def test_connection_uri_env_wins(self):
        env = {"CDS_DB_ANALYTICS_CONNECTION_URI": "postgresql://u:p@db:5432/analytics"}
        self.assertEqual(
            connections.database("analytics", env=env).connection_uri,
            "postgresql://u:p@db:5432/analytics",
        )

    def test_postgres_uri_is_assembled_with_quoted_credentials(self):
        env = {
            "CDS_DB_ANALYTICS_BACKEND": "postgres",
            "CDS_DB_ANALYTICS_HOST": "db",
            "CDS_DB_ANALYTICS_PORT": "5433",
            "CDS_DB_ANALYTICS_DATABASE": "ana lytics",
            "CDS_DB_ANALYTICS_USERNAME": "u ser",
            "CDS_DB_ANALYTICS_PASSWORD": "p@ss word",
        }
        self.assertEqual(
            connections.database("analytics", env=env).connection_uri,
            "postgresql+psycopg2://u+ser:p%40ss+word@db:5433/ana+lytics",
        )

    def test_postgres_default_port(self):
        env = {
            "CDS_DB_ANALYTICS_HOST": "db",
            "CDS_DB_ANALYTICS_DATABASE": "d",
            "CDS_DB_ANALYTICS_USERNAME": "u",
            "CDS_DB_ANALYTICS_PASSWORD": "p",
        }
        self.assertEqual(
            connections.database("analytics", env=env).connection_uri,
            "postgresql+psycopg2://u:p@db:5432/d",
        )

    def test_mysql_uri_uses_the_pymysql_driver(self):
        env = {
            "CDS_DB_ANALYTICS_BACKEND": "mysql",
            "CDS_DB_ANALYTICS_HOST": "db",
            "CDS_DB_ANALYTICS_DATABASE": "d",
            "CDS_DB_ANALYTICS_USERNAME": "u",
            "CDS_DB_ANALYTICS_PASSWORD": "p",
        }
        self.assertEqual(
            connections.database("analytics", env=env).connection_uri,
            "mysql+pymysql://u:p@db:3306/d",
        )

    def test_invalid_port_is_rejected(self):
        env = {
            "CDS_DB_ANALYTICS_BACKEND": "postgres",
            "CDS_DB_ANALYTICS_HOST": "db",
            "CDS_DB_ANALYTICS_PORT": "not-a-port",
            "CDS_DB_ANALYTICS_DATABASE": "d",
            "CDS_DB_ANALYTICS_USERNAME": "u",
            "CDS_DB_ANALYTICS_PASSWORD": "p",
        }
        with self.assertRaises(MissingConnectionSettingsError) as ctx:
            uri = connections.database("analytics", env=env).connection_uri
            self.assertTrue(uri)
        self.assertIn("CDS_DB_ANALYTICS_PORT", str(ctx.exception))
        self.assertNotIn("not-a-port", str(ctx.exception))

    def test_sqlite_uri_from_path_without_explicit_backend(self):
        env = {"CDS_DB_WAREHOUSE_SQLITE_PATH": "/tmp/warehouse.db"}
        self.assertEqual(
            connections.database("warehouse", env=env).connection_uri,
            "sqlite:////tmp/warehouse.db",
        )

    def test_sqlite_memory(self):
        env = {"CDS_DB_WAREHOUSE_BACKEND": "sqlite", "CDS_DB_WAREHOUSE_SQLITE_PATH": ":memory:"}
        self.assertEqual(
            connections.database("warehouse", env=env).connection_uri,
            "sqlite:///:memory:",
        )

    def test_unsupported_backend_reports_missing_settings(self):
        env = {"CDS_DB_WAREHOUSE_BACKEND": "oracle"}
        with self.assertRaises(MissingConnectionSettingsError):
            uri = connections.database("warehouse", env=env).connection_uri
            self.assertTrue(uri)

    def test_missing_settings_error_names_variables_not_values(self):
        env = {
            "CDS_DB_ANALYTICS_BACKEND": "postgres",
            "CDS_DB_ANALYTICS_HOST": "db",
            "CDS_DB_ANALYTICS_PASSWORD": "super-secret",
        }
        with self.assertRaises(MissingConnectionSettingsError) as ctx:
            uri = connections.database("analytics", env=env).connection_uri
            self.assertTrue(uri)
        message = str(ctx.exception)
        self.assertIn("CDS_DB_ANALYTICS_DATABASE", message)
        self.assertIn("CDS_DB_ANALYTICS_USERNAME", message)
        self.assertNotIn("super-secret", message)

    def test_empty_password_selects_passwordless_auth(self):
        env = {
            "CDS_DB_ANALYTICS_BACKEND": "postgres",
            "CDS_DB_ANALYTICS_HOST": "db",
            "CDS_DB_ANALYTICS_DATABASE": "d",
            "CDS_DB_ANALYTICS_USERNAME": "u",
            "CDS_DB_ANALYTICS_PASSWORD": "",
        }
        self.assertEqual(
            connections.database("analytics", env=env).connection_uri,
            "postgresql+psycopg2://u:@db:5432/d",
        )

    def test_unset_password_is_reported_as_missing(self):
        env = {
            "CDS_DB_ANALYTICS_BACKEND": "postgres",
            "CDS_DB_ANALYTICS_HOST": "db",
            "CDS_DB_ANALYTICS_DATABASE": "d",
            "CDS_DB_ANALYTICS_USERNAME": "u",
        }
        with self.assertRaises(MissingConnectionSettingsError) as ctx:
            uri = connections.database("analytics", env=env).connection_uri
            self.assertTrue(uri)
        self.assertIn("CDS_DB_ANALYTICS_PASSWORD", str(ctx.exception))

    def test_non_numeric_port_is_rejected(self):
        env = {
            "CDS_DB_ANALYTICS_BACKEND": "postgres",
            "CDS_DB_ANALYTICS_HOST": "db",
            "CDS_DB_ANALYTICS_PORT": "abc",
            "CDS_DB_ANALYTICS_DATABASE": "d",
            "CDS_DB_ANALYTICS_USERNAME": "u",
            "CDS_DB_ANALYTICS_PASSWORD": "p",
        }
        with self.assertRaises(MissingConnectionSettingsError) as ctx:
            uri = connections.database("analytics", env=env).connection_uri
            self.assertTrue(uri)
        self.assertIn("CDS_DB_ANALYTICS_PORT", str(ctx.exception))
        self.assertNotIn("abc", str(ctx.exception))

    def test_legacy_name_and_user_fields_resolve_as_aliases(self):
        # The dagster module's environment block predates the canonical
        # spellings; adopting it field-wise must work without renaming.
        env = {
            "CDS_ANALYTICS_DB_BACKEND": "postgres",
            "CDS_ANALYTICS_DB_HOST": "db",
            "CDS_ANALYTICS_DB_PORT": "5433",
            "CDS_ANALYTICS_DB_NAME": "analytics",
            "CDS_ANALYTICS_DB_USER": "u",
            "CDS_ANALYTICS_DB_PASSWORD": "p",
        }
        self.assertEqual(
            connections.database("analytics", prefix="CDS_ANALYTICS_DB", env=env).connection_uri,
            "postgresql+psycopg2://u:p@db:5433/analytics",
        )

    def test_legacy_fields_alone_count_as_configuration(self):
        # A legacy-only block is not mistaken for an unknown connection.
        env = {"CDS_ANALYTICS_DB_NAME": "analytics"}
        with self.assertRaises(MissingConnectionSettingsError) as ctx:
            uri = connections.database("analytics", prefix="CDS_ANALYTICS_DB", env=env).connection_uri
            self.assertTrue(uri)
        self.assertIn("CDS_ANALYTICS_DB_HOST", str(ctx.exception))

    def test_port_out_of_range_is_rejected(self):
        for bad_port in ("0", "99999"):
            with self.subTest(port=bad_port):
                env = {
                    "CDS_DB_ANALYTICS_BACKEND": "postgres",
                    "CDS_DB_ANALYTICS_HOST": "db",
                    "CDS_DB_ANALYTICS_PORT": bad_port,
                    "CDS_DB_ANALYTICS_DATABASE": "d",
                    "CDS_DB_ANALYTICS_USERNAME": "u",
                    "CDS_DB_ANALYTICS_PASSWORD": "p",
                }
                with self.assertRaises(MissingConnectionSettingsError):
                    uri = connections.database("analytics", env=env).connection_uri
                    self.assertTrue(uri)

    def test_unknown_connection_reports_expected_variables(self):
        with self.assertRaises(UnknownConnectionError) as ctx:
            uri = connections.database("ghost", env={"OTHER": "x"}).connection_uri
            self.assertTrue(uri)
        self.assertIn("CDS_DB_GHOST_CONNECTION_URI", str(ctx.exception))

    def test_partial_prefix_matches_do_not_shadow(self):
        # A connection whose prefix merely shares a stem with a configured
        # one is still unknown.
        with self.assertRaises(UnknownConnectionError):
            uri = connections.database("analytics", env={"CDS_DB_ANALYTICS_DB_HOST": "h"}).connection_uri
            self.assertTrue(uri)


class RegistryTest(unittest.TestCase):
    def setUp(self):
        connections.close_all()

    def tearDown(self):
        connections.close_all()

    def test_same_name_shares_one_connection(self):
        first = connections.database("analytics")
        second = connections.database("analytics")
        self.assertIs(first, second)

    def test_same_prefix_with_different_names_stays_distinct(self):
        first = connections.database("x")
        second = connections.database("analytics", prefix="CDS_DB_X")
        self.assertIsNot(first, second)
        self.assertEqual(first.name, "x")
        self.assertEqual(second.name, "analytics")

    def test_explicit_env_bypasses_the_registry(self):
        first = connections.database("analytics", env={"CDS_DB_ANALYTICS_CONNECTION_URI": "sqlite:///:memory:"})
        second = connections.database("analytics", env={"CDS_DB_ANALYTICS_CONNECTION_URI": "sqlite:///:memory:"})
        self.assertIsNot(first, second)

    def test_close_all_disposes_and_clears(self):
        class Tracked(DatabaseConnection):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.closed = False

            def close(self):
                self.closed = True
                super().close()

        tracked = Tracked("analytics", env={"CDS_DB_ANALYTICS_CONNECTION_URI": "sqlite:///:memory:"})
        connections._connections[("analytics", "CDS_DB_ANALYTICS")] = tracked
        connections.close_all()
        self.assertTrue(tracked.closed)
        self.assertEqual(connections._connections, {})


class RedactionTest(unittest.TestCase):
    def test_redact_uri_masks_password(self):
        self.assertEqual(
            redact_uri("postgresql://user:secret@host:5432/db"),
            "postgresql://user:***@host:5432/db",
        )

    def test_redact_uri_masks_secret_query_parameters(self):
        redacted = redact_uri("mysql+pymysql://u:p@h/db?token=abc&flag=1")
        self.assertNotIn("abc", redacted)
        self.assertIn("flag=1", redacted)

    def test_redact_uris_in_text_masks_embedded_uris(self):
        message = "could not reach postgresql://user:hunter2@db:5432/x on host db"
        redacted = redact_uris_in_text(message)
        self.assertNotIn("hunter2", redacted)
        self.assertIn("user:***@db:5432/x", redacted)

    def test_query_error_message_redacts_embedded_uris(self):
        connection = DatabaseConnection(
            "analytics", env={"CDS_DB_ANALYTICS_CONNECTION_URI": "postgresql://user:secret@host/db"}
        )
        message = connection._redacted_message(
            Exception("could not reach postgresql://user:hunter2@db:5432/x")
        )
        self.assertNotIn("hunter2", message)
        self.assertNotIn("secret", message)
        self.assertIn("***", message)

    def test_connection_repr_carries_no_configuration(self):
        env = {"CDS_DB_ANALYTICS_CONNECTION_URI": "postgresql://user:secret@host:5432/db"}
        connection = connections.database("analytics", env=env)
        self.assertEqual(repr(connection), "DatabaseConnection(name='analytics')")
        self.assertNotIn("secret", repr(connection))

    def test_redacted_connection_uri(self):
        env = {"CDS_DB_ANALYTICS_CONNECTION_URI": "postgresql://user:secret@host:5432/db"}
        self.assertEqual(
            connections.database("analytics", env=env).redacted_connection_uri,
            "postgresql://user:***@host:5432/db",
        )


class QueryErrorRedactionTest(unittest.TestCase):
    """Secret redaction in wrapped driver failures, without a live engine."""

    def _connection(self, env):
        return connections.database("c", env=env)

    def test_error_message_redacts_password_field(self):
        error = self._connection({"CDS_DB_C_PASSWORD": "super-secret"})._query_error(
            RuntimeError("authentication failed for super-secret")
        )
        self.assertNotIn("super-secret", str(error))

    def test_error_message_redacts_password_embedded_in_connection_uri(self):
        env = {"CDS_DB_C_CONNECTION_URI": "postgresql://user:hunter2@db:5432/d"}
        error = self._connection(env)._query_error(
            RuntimeError("could not connect to postgresql://user:hunter2@db:5432/d")
        )
        message = str(error)
        self.assertNotIn("hunter2", message)
        self.assertIn("***", message)

    def test_error_message_redacts_uri_password_without_uri_shape(self):
        env = {"CDS_DB_C_CONNECTION_URI": "postgresql://user:hunter2@db:5432/d"}
        error = self._connection(env)._query_error(RuntimeError("password hunter2 rejected"))
        self.assertNotIn("hunter2", str(error))

    def test_short_secrets_are_not_substituted(self):
        # A 1-character password must not mangle unrelated words in the
        # driver message ("product" stays "product").
        error = self._connection({"CDS_DB_C_PASSWORD": "p"})._query_error(
            RuntimeError('column "product" does not exist')
        )
        self.assertIn("product", str(error))
        self.assertNotIn("***roduct", str(error))


class DriverAvailabilityTest(unittest.TestCase):
    def test_missing_sqlalchemy_raises_typed_error(self):
        connection = DatabaseConnection(
            "analytics", env={"CDS_DB_ANALYTICS_CONNECTION_URI": "sqlite:///:memory:"}
        )
        if SQLALCHEMY_AVAILABLE:
            self.skipTest("SQLAlchemy is installed in this environment")
        with self.assertRaises(DriverUnavailableError):
            connection.fetch_all("SELECT 1")


class ApiGuardTest(unittest.TestCase):
    def test_exported_surface(self):
        import cds_runtime

        for name in ("connections", "database", "close_all", "redact_uri", "env_prefix_for"):
            self.assertTrue(hasattr(cds_runtime, name), name)
        for error in (CdsRuntimeError, UnknownConnectionError, MissingConnectionSettingsError,
                      DriverUnavailableError, ConnectionSetupError, QueryExecutionError):
            self.assertTrue(issubclass(error, CdsRuntimeError))

    def test_top_level_connections_module_is_the_registry_module(self):
        import cds_runtime.connections as via_attribute

        self.assertIs(via_attribute.database, connections.database)

    def test_dagster_adapter_returns_connection_uri(self):
        env = {"CDS_DB_ANALYTICS_DATABASE_CONNECTION_URI": "postgresql://u:p@db:5432/analytics"}
        self.assertEqual(
            dagster.postgres_url("analytics-database", env=env),
            "postgresql://u:p@db:5432/analytics",
        )

    def test_dagster_adapter_rejects_non_postgres_url(self):
        env = {"CDS_DB_ANALYTICS_DATABASE_CONNECTION_URI": "mysql+pymysql://u:p@db:3306/d"}
        with self.assertRaises(ValueError) as ctx:
            dagster.postgres_url("analytics-database", env=env)
        self.assertIn("analytics-database", str(ctx.exception))

    def test_resolve_connection_uri_is_exposed(self):
        env = {"CDS_DB_X_CONNECTION_URI": "sqlite:///:memory:"}
        self.assertEqual(resolve_connection_uri("CDS_DB_X", env), "sqlite:///:memory:")


@unittest.skipIf(not SQLALCHEMY_AVAILABLE, "SQLAlchemy is not installed in this environment")
class DatabaseOperationsTest(unittest.TestCase):
    """The data path, exercised against a real (SQLite) engine."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.env = {
            "CDS_DB_ANALYTICS_BACKEND": "sqlite",
            "CDS_DB_ANALYTICS_SQLITE_PATH": str(Path(self._tmp.name) / "analytics.db"),
            "CDS_DB_ANALYTICS_PASSWORD": "super-secret",
        }
        self.db = connections.database("analytics", env=self.env)
        self.db.execute(
            "CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT, active INTEGER)"
        )

    def tearDown(self):
        self.db.close()

    def test_issue_example_read_and_write(self):
        self.db.execute(
            "INSERT INTO customers (name, active) VALUES (:name, :active)",
            {"name": "acme", "active": 1},
        )
        rows = self.db.fetch_all(
            "SELECT * FROM customers WHERE active = :active", {"active": 1}
        )
        self.assertEqual(rows, [{"id": 1, "name": "acme", "active": 1}])

    def test_fetch_one_returns_none_without_rows(self):
        self.assertIsNone(self.db.fetch_one("SELECT * FROM customers"))

    def test_insert_rows_bulk_inserts_and_counts(self):
        rows = [{"name": f"n{i}", "active": 0} for i in range(10)]
        inserted = self.db.insert_rows("customers", rows, batch_size=3)
        self.assertEqual(inserted, 10)
        self.assertEqual(len(self.db.fetch_all("SELECT * FROM customers")), 10)

    def test_insert_rows_empty_rowset_inserts_nothing(self):
        self.assertEqual(self.db.insert_rows("customers", []), 0)

    def test_insert_rows_rejects_inconsistent_rows(self):
        with self.assertRaises(ValueError):
            self.db.insert_rows("customers", [{"name": "a", "active": 1}, {"name": "b"}])

    def test_insert_rows_rejects_injection_through_identifiers(self):
        with self.assertRaises(ValueError):
            self.db.insert_rows("customers; DROP TABLE customers", [{"name": "a"}])
        with self.assertRaises(ValueError):
            self.db.insert_rows("customers", [{"name; DROP": "a"}])

    def test_insert_rows_quotes_reserved_words_through_the_dialect(self):
        self.db.execute('CREATE TABLE "order" ("select" TEXT)')
        self.db.insert_rows("order", [{"select": "a"}])
        rows = self.db.fetch_all('SELECT * FROM "order"')
        self.assertEqual(rows, [{"select": "a"}])

    def test_transaction_commits_on_success(self):
        with self.db.transaction() as tx:
            tx.execute("INSERT INTO customers (name, active) VALUES (:n, :a)", {"n": "t1", "a": 1})
            tx.execute("INSERT INTO customers (name, active) VALUES (:n, :a)", {"n": "t2", "a": 1})
        self.assertEqual(len(self.db.fetch_all("SELECT * FROM customers")), 2)

    def test_transaction_rolls_back_on_exception(self):
        with self.assertRaises(RuntimeError):
            with self.db.transaction() as tx:
                tx.execute("INSERT INTO customers (name, active) VALUES (:n, :a)", {"n": "t", "a": 1})
                raise RuntimeError("boom")
        self.assertEqual(self.db.fetch_all("SELECT * FROM customers"), [])

    def test_transaction_fetch_operations_share_the_transaction(self):
        self.db.execute("INSERT INTO customers (name, active) VALUES (:n, :a)", {"n": "x", "a": 1})
        with self.db.transaction() as tx:
            row = tx.fetch_one("SELECT * FROM customers WHERE name = :n", {"n": "x"})
            self.assertEqual(row["name"], "x")
            tx.execute("UPDATE customers SET active = :a WHERE name = :n", {"a": 0, "n": "x"})

    def test_query_error_is_typed_and_redacted(self):
        with self.assertRaises(QueryExecutionError) as ctx:
            self.db.execute("SELECT * FROM nonexistent_table")
        message = str(ctx.exception)
        self.assertIn("analytics", message)
        self.assertNotIn("super-secret", message)
        self.assertNotIn("sqlite://", message)

    def test_engine_creation_failure_is_typed_and_redacted(self):
        broken = connections.database(
            "broken",
            env={"CDS_DB_BROKEN_CONNECTION_URI": "post gresql://user:hunter2@host/db"},
        )
        with self.assertRaises(ConnectionSetupError) as ctx:
            broken.fetch_all("SELECT 1")
        message = str(ctx.exception)
        self.assertIn("broken", message)
        self.assertNotIn("hunter2", message)

    def test_close_disposes_and_reconnects_lazily(self):
        self.db.execute("INSERT INTO customers (name, active) VALUES (:n, :a)", {"n": "a", "a": 1})
        self.db.close()
        self.db.close()  # idempotent
        self.assertEqual(len(self.db.fetch_all("SELECT * FROM customers")), 1)

    def test_context_manager_closes_on_exit(self):
        with connections.database("analytics", env=self.env) as db:
            db.execute("INSERT INTO customers (name, active) VALUES (:n, :a)", {"n": "a", "a": 1})
            engine = db.engine()
        self.assertIsNotNone(engine)
        self.assertIsNone(db._engine)

    def test_empty_statement_is_rejected(self):
        with self.assertRaises(ValueError):
            self.db.execute("   ")


class ConcurrencyTest(unittest.TestCase):
    """The documented thread model: one shared connection, many threads.

    The registry and lazy engine creation are deliberately not
    synchronized (see the Limitations section of docs/runtime-api.md);
    what must hold is that concurrent operations through the one
    registered connection all succeed.
    """

    _ENV_KEYS = ("CDS_DB_CONCURRENT_BACKEND", "CDS_DB_CONCURRENT_SQLITE_PATH")

    def setUp(self):
        connections.close_all()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._saved = {key: os.environ.get(key) for key in self._ENV_KEYS}
        os.environ["CDS_DB_CONCURRENT_BACKEND"] = "sqlite"
        os.environ["CDS_DB_CONCURRENT_SQLITE_PATH"] = str(Path(self._tmp.name) / "concurrent.db")
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        connections.close_all()
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_threads_share_one_registered_connection(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = [pool.submit(connections.database, "concurrent") for _ in range(64)]
            objects = [future.result() for future in results]
        self.assertTrue(all(obj is objects[0] for obj in objects), "registry returned different objects")

    def test_concurrent_operations_on_one_connection(self):
        db = connections.database("concurrent")
        db.execute("CREATE TABLE hits (id INTEGER PRIMARY KEY)")
        db.execute("INSERT INTO hits (id) VALUES (:i)", {"i": 0})
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(db.fetch_one, "SELECT id FROM hits") for _ in range(64)]
            for future in futures:
                self.assertEqual(future.result(), {"id": 0})
        self.assertEqual(db.fetch_all("SELECT * FROM hits"), [{"id": 0}])


if __name__ == "__main__":
    unittest.main()
