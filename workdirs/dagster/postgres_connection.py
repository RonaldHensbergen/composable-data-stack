import importlib
import json
import os
from collections.abc import Mapping
from urllib.parse import quote_plus


def get_postgres_connection_uri(
    prefix: str = "ANALYTICS_DB", env: Mapping[str, str] | None = None
) -> str:
    """Build a PostgreSQL connection URI from environment variables.

    Preferred input is ``<PREFIX>_CONNECTION_URI``. If that is not present,
    the URI is assembled from host, port, database name, username, and password.
    """

    values = os.environ if env is None else env

    connection_uri = values.get(f"{prefix}_CONNECTION_URI")
    if connection_uri:
        return connection_uri

    host = values.get(f"{prefix}_HOST")
    port = values.get(f"{prefix}_PORT", "5432")
    database = values.get(f"{prefix}_NAME")
    username = values.get(f"{prefix}_USER")
    password = values.get(f"{prefix}_PASSWORD")

    missing = [
        name
        for name, value in {
            f"{prefix}_HOST": host,
            f"{prefix}_NAME": database,
            f"{prefix}_USER": username,
            f"{prefix}_PASSWORD": password,
        }.items()
        if not value
    ]
    if missing:
        missing_list = ", ".join(missing)
        raise RuntimeError(f"Missing PostgreSQL connection settings: {missing_list}")

    quoted_username = quote_plus(username)
    quoted_password = quote_plus(password)
    quoted_database = quote_plus(database)
    return f"postgresql://{quoted_username}:{quoted_password}@{host}:{port}/{quoted_database}"


def get_sqlalchemy_module():
    try:
        return importlib.import_module("sqlalchemy")
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "sqlalchemy is required to create PostgreSQL connections from Python code"
        ) from exc


def get_postgres_engine(prefix: str = "ANALYTICS_DB", env=None, **engine_kwargs):
    sqlalchemy = get_sqlalchemy_module()
    connection_uri = get_postgres_connection_uri(prefix=prefix, env=env)
    return sqlalchemy.create_engine(connection_uri, **engine_kwargs)


def get_postgres_connection(prefix: str = "ANALYTICS_DB", env=None, **engine_kwargs):
    engine = get_postgres_engine(prefix=prefix, env=env, **engine_kwargs)
    return engine.connect()


def sql_text(statement: str):
    sqlalchemy = get_sqlalchemy_module()
    return sqlalchemy.text(statement)


def insert_incoming_file_event(
    payload: Mapping[str, object],
    asset_key: str,
    prefix: str = "ANALYTICS_DB",
    env: Mapping[str, str] | None = None,
) -> None:
    values = os.environ if env is None else env
    if not values.get(f"{prefix}_CONNECTION_URI") and not values.get(f"{prefix}_HOST"):
        return

    engine = get_postgres_engine(prefix=prefix, env=env)
    with engine.begin() as connection:
        connection.execute(
            sql_text(
                """
                CREATE TABLE IF NOT EXISTS incoming_file_events (
                    id BIGSERIAL PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    asset_key TEXT NOT NULL,
                    file_name TEXT,
                    incoming_dir TEXT,
                    processed_dir TEXT,
                    content TEXT,
                    metadata_json JSONB NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
        )
        connection.execute(
            sql_text(
                """
                INSERT INTO incoming_file_events (
                    event_type,
                    asset_key,
                    file_name,
                    incoming_dir,
                    processed_dir,
                    content,
                    metadata_json
                ) VALUES (
                    :event_type,
                    :asset_key,
                    :file_name,
                    :incoming_dir,
                    :processed_dir,
                    :content,
                    CAST(:metadata_json AS JSONB)
                )
                """
            ),
            {
                "event_type": str(payload.get("event", asset_key)),
                "asset_key": asset_key,
                "file_name": payload.get("file_name"),
                "incoming_dir": payload.get("incoming_dir"),
                "processed_dir": payload.get("processed_dir"),
                "content": payload.get("content"),
                "metadata_json": json.dumps(dict(payload)),
            },
        )