import importlib.util
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFINITIONS_PATH = REPO_ROOT / "workdirs" / "dagster" / "definitions.py"


def _load_definitions_module():
    dagster_mock = unittest.mock.MagicMock()
    sys.modules["dagster"] = dagster_mock
    sys.modules["psycopg2"] = unittest.mock.MagicMock()
    sys.modules["psycopg2.sql"] = unittest.mock.MagicMock()
    sys.modules["psycopg2.extras"] = unittest.mock.MagicMock()

    spec = importlib.util.spec_from_file_location("cds_dagster_definitions", DEFINITIONS_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ResolveTargetDbUriTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._saved_modules = {
            name: sys.modules.get(name)
            for name in ("dagster", "psycopg2", "psycopg2.sql", "psycopg2.extras")
        }
        cls.definitions = _load_definitions_module()

    @classmethod
    def tearDownClass(cls) -> None:
        for name, module in cls._saved_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    def test_uri_encodes_special_chars_in_password(self) -> None:
        with patch.dict(
            os.environ,
            {
                "CDS_ANALYTICS_DB_NAME": "analytics",
                "CDS_ANALYTICS_DB_USER": "user",
                "CDS_ANALYTICS_DB_PASSWORD": "p@ss:word/123",
                "CDS_ANALYTICS_DB_HOST": "postgres",
                "CDS_ANALYTICS_DB_PORT": "5432",
            },
            clear=True,
        ):
            uri = self.definitions._resolve_target_db_uri()
        self.assertEqual(
            uri,
            "postgresql://user:p%40ss%3Aword%2F123@postgres:5432/analytics",
        )

    def test_uri_encodes_special_chars_in_user(self) -> None:
        with patch.dict(
            os.environ,
            {
                "CDS_ANALYTICS_DB_NAME": "analytics",
                "CDS_ANALYTICS_DB_USER": "us er",
                "CDS_ANALYTICS_DB_PASSWORD": "secret",
                "CDS_ANALYTICS_DB_HOST": "postgres",
                "CDS_ANALYTICS_DB_PORT": "5432",
            },
            clear=True,
        ):
            uri = self.definitions._resolve_target_db_uri()
        self.assertEqual(
            uri,
            "postgresql://us%20er:secret@postgres:5432/analytics",
        )

    def test_uri_encodes_special_chars_in_db_name(self) -> None:
        with patch.dict(
            os.environ,
            {
                "CDS_ANALYTICS_DB_NAME": "my db",
                "CDS_ANALYTICS_DB_USER": "user",
                "CDS_ANALYTICS_DB_PASSWORD": "secret",
                "CDS_ANALYTICS_DB_HOST": "postgres",
                "CDS_ANALYTICS_DB_PORT": "5432",
            },
            clear=True,
        ):
            uri = self.definitions._resolve_target_db_uri()
        self.assertEqual(
            uri,
            "postgresql://user:secret@postgres:5432/my%20db",
        )

    def test_uri_normal_values_unchanged(self) -> None:
        with patch.dict(
            os.environ,
            {
                "CDS_ANALYTICS_DB_NAME": "analytics",
                "CDS_ANALYTICS_DB_USER": "user",
                "CDS_ANALYTICS_DB_PASSWORD": "secret",
                "CDS_ANALYTICS_DB_HOST": "postgres",
                "CDS_ANALYTICS_DB_PORT": "5432",
            },
            clear=True,
        ):
            uri = self.definitions._resolve_target_db_uri()
        self.assertEqual(
            uri,
            "postgresql://user:secret@postgres:5432/analytics",
        )

    def test_explicit_uri_takes_priority(self) -> None:
        with patch.dict(
            os.environ,
            {
                "CDS_TARGET_DB_CONNECTION_URI": "postgresql://explicit:uri@host:5432/db",
                "CDS_ANALYTICS_DB_NAME": "analytics",
                "CDS_ANALYTICS_DB_USER": "user",
                "CDS_ANALYTICS_DB_PASSWORD": "secret",
            },
            clear=True,
        ):
            uri = self.definitions._resolve_target_db_uri()
        self.assertEqual(uri, "postgresql://explicit:uri@host:5432/db")

    def test_missing_config_raises(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError):
                self.definitions._resolve_target_db_uri()


if __name__ == "__main__":
    unittest.main()
