"""Integration tests for the cds-runtime database API against real backends.

Opt-in: each backend runs only when its environment variable provides a
live database URL, so the suite stays green without services (CI covers
them via follow-up wiring). Example setup:

    docker run -d --rm --name cds-it-pg -e POSTGRES_USER=ituser \
        -e POSTGRES_PASSWORD=itpass -e POSTGRES_DB=itdb \
        -p 127.0.0.1:55432:5432 postgres:16-alpine
    export CDS_IT_POSTGRES_URI=postgresql://ituser:itpass@127.0.0.1:55432/itdb

    docker run -d --rm --name cds-it-mysql -e MYSQL_DATABASE=itdb \
        -e MYSQL_USER=ituser -e MYSQL_PASSWORD=itpass \
        -e MYSQL_ROOT_PASSWORD=rootpass -p 127.0.0.1:55330:3306 mysql:8
    export CDS_IT_MYSQL_URI=mysql+pymysql://ituser:itpass@127.0.0.1:55330/itdb
"""
import sys
import unittest
from os import environ
from pathlib import Path
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parent.parent
RUNTIME_ROOT = REPO_ROOT / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from cds_runtime import connections  # noqa: E402
from cds_runtime.errors import QueryExecutionError  # noqa: E402


class BackendIntegrationTest:
    """Provider-neutral data-path coverage against one live backend.

    Mixin: concrete subclasses pair this with ``unittest.TestCase`` and
    set ``backend`` and ``uri`` (a live connection URL).
    """

    backend = ""  # set by subclasses ("postgres" / "mysql")
    uri = ""  # set by subclasses (live connection URL)

    @classmethod
    def setUpClass(cls):
        parts = urlsplit(cls.uri)
        cls.quote_char = "`" if cls.backend == "mysql" else '"'
        cls.env = {
            "CDS_DB_IT_BACKEND": cls.backend,
            "CDS_DB_IT_HOST": parts.hostname,
            "CDS_DB_IT_PORT": str(parts.port),
            "CDS_DB_IT_DATABASE": parts.path.lstrip("/"),
            "CDS_DB_IT_USERNAME": parts.username,
            "CDS_DB_IT_PASSWORD": parts.password,
        }
        cls.db = connections.database("it", env=cls.env)
        cls.db.execute("DROP TABLE IF EXISTS customers")
        cls.db.execute("CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT, active INTEGER)")

    @classmethod
    def tearDownClass(cls):
        cls.db.execute("DROP TABLE IF EXISTS customers")
        cls.db.close()

    def test_field_assembled_uri_connects(self):
        # The fields were assembled into a URI without CONNECTION_URI; a
        # successful query proves the assembled URL is valid.
        self.assertEqual(self.db.fetch_one("SELECT 1 AS one"), {"one": 1})

    def test_connection_uri_variable_wins(self):
        db = connections.database("it", env={"CDS_DB_IT_CONNECTION_URI": self.uri})
        try:
            self.assertEqual(db.fetch_one("SELECT 1 AS one"), {"one": 1})
        finally:
            db.close()

    def test_fetch_and_execute_roundtrip(self):
        self.db.execute("DELETE FROM customers")
        self.db.execute(
            "INSERT INTO customers (id, name, active) VALUES (:id, :name, :active)",
            {"id": 1, "name": "acme", "active": 1},
        )
        rows = self.db.fetch_all("SELECT id, name, active FROM customers WHERE active = :a", {"a": 1})
        self.assertEqual(rows, [{"id": 1, "name": "acme", "active": 1}])
        row = self.db.fetch_one("SELECT name FROM customers WHERE id = :i", {"i": 1})
        self.assertEqual(row, {"name": "acme"})
        self.assertIsNone(self.db.fetch_one("SELECT * FROM customers WHERE id = :i", {"i": 999}))

    def test_execute_reports_rowcount(self):
        self.db.execute("DELETE FROM customers")
        for i in range(1, 4):
            self.db.execute(
                "INSERT INTO customers (id, name, active) VALUES (:i, :n, :a)",
                {"i": i, "n": f"n{i}", "a": 1},
            )
        updated = self.db.execute(
            "UPDATE customers SET active = :a WHERE id <= :i", {"a": 0, "i": 2}
        )
        self.assertEqual(updated, 2)

    def test_insert_rows_bulk_and_reserved_word_identifiers(self):
        # Reserved-word table and column names prove dialect-aware quoting:
        # backticks on MySQL, double quotes on PostgreSQL/SQLite.
        q = self.quote_char
        self.db.execute(f"DROP TABLE IF EXISTS {q}order{q}")
        self.db.execute(f"CREATE TABLE {q}order{q} ({q}select{q} TEXT, n INTEGER)")
        try:
            rows = [{"select": f"s{i}", "n": i} for i in range(25)]
            inserted = self.db.insert_rows("order", rows, batch_size=7)
            self.assertEqual(inserted, 25)
            total = self.db.fetch_one(f"SELECT COUNT(*) AS c FROM {q}order{q}")  # noqa: S608
            self.assertEqual(total["c"], 25)
        finally:
            self.db.execute(f"DROP TABLE {q}order{q}")

    def test_transaction_commit_and_rollback(self):
        self.db.execute("DELETE FROM customers")
        with self.db.transaction() as tx:
            tx.execute(
                "INSERT INTO customers (id, name, active) VALUES (:i, :n, :a)",
                {"i": 10, "n": "t1", "a": 1},
            )
        self.assertEqual(len(self.db.fetch_all("SELECT * FROM customers")), 1)

        with self.assertRaises(RuntimeError):
            with self.db.transaction() as tx:
                tx.execute(
                    "INSERT INTO customers (id, name, active) VALUES (:i, :n, :a)",
                    {"i": 11, "n": "t2", "a": 1},
                )
                raise RuntimeError("boom")
        self.assertEqual(len(self.db.fetch_all("SELECT * FROM customers")), 1)

    def test_query_failure_is_typed_and_redacted(self):
        with self.assertRaises(QueryExecutionError) as ctx:
            self.db.fetch_all("SELECT * FROM definitely_missing_table")
        message = str(ctx.exception)
        self.assertIn("it", message)
        self.assertNotIn(self.env["CDS_DB_IT_PASSWORD"], message)

    def test_insert_rows_rejects_injection_before_connecting(self):
        with self.assertRaises(ValueError):
            self.db.insert_rows("customers; DROP TABLE customers", [{"id": 1}])


@unittest.skipUnless(environ.get("CDS_IT_POSTGRES_URI"), "CDS_IT_POSTGRES_URI is not set")
class PostgresIntegrationTest(BackendIntegrationTest, unittest.TestCase):
    backend = "postgres"
    uri = environ.get("CDS_IT_POSTGRES_URI", "")


@unittest.skipUnless(environ.get("CDS_IT_MYSQL_URI"), "CDS_IT_MYSQL_URI is not set")
class MysqlIntegrationTest(BackendIntegrationTest, unittest.TestCase):
    backend = "mysql"
    uri = environ.get("CDS_IT_MYSQL_URI", "")
