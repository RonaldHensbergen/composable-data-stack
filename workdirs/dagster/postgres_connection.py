import importlib


try:
    _db_connection = importlib.import_module("shared.python.db.connection")
except ModuleNotFoundError:
    _db_connection = importlib.import_module("connection")

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