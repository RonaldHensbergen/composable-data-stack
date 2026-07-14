import importlib
import importlib.util
from pathlib import Path


try:
    _db_connection = importlib.import_module("shared.python.db.connection")
except ModuleNotFoundError:
    shared_connection_path = Path(__file__).resolve().parents[2] / "shared" / "python" / "db" / "connection.py"
    spec = importlib.util.spec_from_file_location("cds_shared_db_connection", shared_connection_path)
    if spec is None or spec.loader is None:
        raise ModuleNotFoundError(f"Unable to load module from {shared_connection_path}")
    _db_connection = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(_db_connection)

get_postgres_connection = _db_connection.get_connection
get_postgres_connection_uri = _db_connection.get_connection_uri
get_postgres_engine = _db_connection.get_engine
get_sqlalchemy_module = _db_connection.get_sqlalchemy_module
insert_incoming_file_event = _db_connection.insert_incoming_file_event
sql_text = _db_connection.sql_text

__all__ = [
    "get_postgres_connection",
    "get_postgres_connection_uri",
    "get_postgres_engine",
    "get_sqlalchemy_module",
    "insert_incoming_file_event",
    "sql_text",
]