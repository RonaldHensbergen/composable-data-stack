import csv
import gzip
import importlib.util
import sys
import tempfile
import types
import unittest
from datetime import date
from pathlib import Path
from unittest import mock


def _load_tender_module():
    repo_root = Path(__file__).resolve().parent.parent
    path = repo_root / "workdirs" / "dagster" / "tender_analytics.py"
    dagster = types.ModuleType("dagster")
    dagster.MetadataValue = mock.MagicMock()
    dagster.asset = lambda **_kwargs: lambda function: function
    dagster.define_asset_job = mock.MagicMock(return_value=object())
    psycopg2 = types.ModuleType("psycopg2")
    with mock.patch.dict(
        sys.modules,
        {"dagster": dagster, "psycopg2": psycopg2},
    ):
        spec = importlib.util.spec_from_file_location("cds_tender_analytics_test", path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        try:
            spec.loader.exec_module(module)
        finally:
            sys.modules.pop(spec.name, None)
    return module


_TENDER_MODULE = _load_tender_module()
TENDER_SNAPSHOT_COLUMNS = _TENDER_MODULE.TENDER_SNAPSHOT_COLUMNS
_UPSERT_SQL = _TENDER_MODULE._UPSERT_SQL
load_tender_snapshot = _TENDER_MODULE.load_tender_snapshot
snapshot_sha256 = _TENDER_MODULE.snapshot_sha256
validate_tender_snapshot = _TENDER_MODULE.validate_tender_snapshot


class _FakeCursor:
    def __init__(self) -> None:
        self.executed: list[str] = []
        self.copied = ""
        self._results = iter(
            [
                (2, 2),
                (2, date(2011, 3, 29), date(2026, 9, 4)),
            ]
        )

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        return None

    def execute(self, statement: str) -> None:
        self.executed.append(statement)

    def copy_expert(self, _statement: str, handle) -> None:
        self.copied = handle.read()

    def fetchone(self):
        return next(self._results)


class _FakeConnection:
    def __init__(self) -> None:
        self.cursor_instance = _FakeCursor()

    def cursor(self) -> _FakeCursor:
        return self.cursor_instance


class TenderAnalyticsTest(unittest.TestCase):
    def _snapshot(self, root: Path, *, columns=TENDER_SNAPSHOT_COLUMNS) -> Path:
        path = root / "tenders.csv.gz"
        rows = [
            {
                "publicatie_id": "TN-1",
                "source_url": "https://example.test/1",
                "kenmerk": "A",
                "aanbesteding_naam": "Road works",
                "opdrachtgever_naam": "Gemeente Test",
                "publicatie_datum": "2011-03-29",
                "type_publicatie": "Aankondiging",
                "type_opdracht": "Werken",
                "procedure": "Openbaar",
                "europees": "true",
                "publicatiecode": "1",
                "publicatiestatus": "Actief",
                "source_ingested_at": "2026-09-04T06:00:00+00:00",
                "chunk_count": "1",
                "has_pdf": "false",
                "cpv_codes": "[]",
                "nuts_codes": "[]",
                "trefwoorden": "[]",
            },
            {
                "publicatie_id": "TN-2",
                "source_url": "https://example.test/2",
                "kenmerk": "B",
                "aanbesteding_naam": "Cloud services",
                "opdrachtgever_naam": "Ministerie Test",
                "publicatie_datum": "2026-09-04",
                "type_publicatie": "Aankondiging",
                "type_opdracht": "Diensten",
                "procedure": "Niet-openbaar",
                "europees": "false",
                "publicatiecode": "2",
                "publicatiestatus": "Actief",
                "source_ingested_at": "2026-09-04T06:00:00+00:00",
                "chunk_count": "3",
                "has_pdf": "true",
                "cpv_codes": '["72"]',
                "nuts_codes": '["NL"]',
                "trefwoorden": '["cloud"]',
            },
        ]
        with gzip.open(path, "wt", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            for row in rows:
                writer.writerow({column: row.get(column, "") for column in columns})
        return path

    def test_validates_exact_snapshot_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            snapshot = self._snapshot(Path(tmpdir))
            validate_tender_snapshot(snapshot)
            self.assertEqual(len(snapshot_sha256(snapshot)), 64)

    def test_rejects_a_snapshot_with_drifted_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            snapshot = self._snapshot(Path(tmpdir), columns=("publicatie_id",))
            with self.assertRaisesRegex(RuntimeError, "expected contract"):
                validate_tender_snapshot(snapshot)

    def test_load_stages_and_upserts_without_deleting_absent_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            snapshot = self._snapshot(Path(tmpdir))
            connection = _FakeConnection()

            result = load_tender_snapshot(connection, snapshot)

        self.assertEqual(result.snapshot_rows, 2)
        self.assertEqual(result.warehouse_rows, 2)
        self.assertEqual(result.earliest_publication, date(2011, 3, 29))
        self.assertIn("TN-1", connection.cursor_instance.copied)
        self.assertIn("ON CONFLICT (publicatie_id) DO UPDATE", _UPSERT_SQL)
        self.assertNotIn("DELETE FROM tender_analytics.tenders", _UPSERT_SQL)

    def test_exporter_enforces_read_only_source_access(self) -> None:
        exporter = (
            Path(__file__).resolve().parent.parent
            / "scripts"
            / "tender"
            / "export-snapshot.sh"
        ).read_text(encoding="utf-8")

        self.assertIn("default_transaction_read_only=on", exporter)
        self.assertIn("statement_timeout=120000", exporter)
        self.assertNotIn("embedding", exporter)
        self.assertNotIn("SELECT content", exporter)

    def test_loader_uses_supported_bounded_dagster_cli(self) -> None:
        loader = (
            Path(__file__).resolve().parent.parent
            / "scripts"
            / "tender"
            / "load-snapshot.sh"
        ).read_text(encoding="utf-8")

        self.assertIn('"$TIMEOUT_BIN" "${WAIT_SECONDS}s"', loader)
        self.assertIn("--python-file /app/workdirs/dagster/definitions.py", loader)
        self.assertNotIn("\n    --file ", loader)


if __name__ == "__main__":
    unittest.main()
