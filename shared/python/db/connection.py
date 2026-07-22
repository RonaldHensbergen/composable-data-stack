import importlib
import json
import os
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus


DEFAULT_PORTS = {
    "postgres": "5432",
    "postgresql": "5432",
    "mysql": "3306",
}


def get_connection_uri(prefix: str = "ANALYTICS_DB", env: Mapping[str, str] | None = None) -> str:
    """Build a SQLAlchemy connection URI from environment variables.

    Preferred input is ``<PREFIX>_CONNECTION_URI``. If that is not present,
    the URI is assembled from ``<PREFIX>_BACKEND`` and backend-specific fields.
    Supported assembled backends are postgres/postgresql, mysql, and sqlite.
    """

    values = os.environ if env is None else env

    connection_uri = values.get(f"{prefix}_CONNECTION_URI")
    if connection_uri:
        return connection_uri

    backend = values.get(f"{prefix}_BACKEND", "postgres").lower()
    if backend in {"postgres", "postgresql", "mysql"}:
        return _build_network_connection_uri(prefix, backend, values)
    if backend == "sqlite":
        return _build_sqlite_connection_uri(prefix, values)

    raise RuntimeError(f"Unsupported database backend for {prefix}: {backend}")


def _build_network_connection_uri(prefix: str, backend: str, values: Mapping[str, str]) -> str:
    host = values.get(f"{prefix}_HOST")
    port = values.get(f"{prefix}_PORT", DEFAULT_PORTS[backend])
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
        raise RuntimeError(f"Missing database connection settings: {missing_list}")

    quoted_username = quote_plus(username)
    quoted_password = quote_plus(password)
    quoted_database = quote_plus(database)
    driver = "postgresql" if backend in {"postgres", "postgresql"} else backend
    return f"{driver}://{quoted_username}:{quoted_password}@{host}:{port}/{quoted_database}"


def _build_sqlite_connection_uri(prefix: str, values: Mapping[str, str]) -> str:
    path = values.get(f"{prefix}_SQLITE_PATH") or values.get(f"{prefix}_PATH")
    if not path:
        raise RuntimeError(
            f"Missing database connection settings: {prefix}_SQLITE_PATH"
        )
    if path == ":memory:":
        return "sqlite:///:memory:"
    if path.startswith("/"):
        return f"sqlite:///{path}"
    return f"sqlite:///{path}"


def get_sqlalchemy_module():
    try:
        return importlib.import_module("sqlalchemy")
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "sqlalchemy is required to create database connections from Python code"
        ) from exc


def get_engine(prefix: str = "ANALYTICS_DB", env=None, **engine_kwargs):
    sqlalchemy = get_sqlalchemy_module()
    connection_uri = get_connection_uri(prefix=prefix, env=env)
    return sqlalchemy.create_engine(connection_uri, **engine_kwargs)


def get_connection(prefix: str = "ANALYTICS_DB", env=None, **engine_kwargs):
    engine = get_engine(prefix=prefix, env=env, **engine_kwargs)
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
    _write_local_event_snapshot(payload, asset_key, values)

    if not values.get(f"{prefix}_CONNECTION_URI") and not _has_backend_settings(prefix, values):
        return

    engine = get_engine(prefix=prefix, env=env)
    connection_uri = get_connection_uri(prefix=prefix, env=env)
    backend = connection_uri.split(":", 1)[0].lower()
    metadata_type = "JSONB" if backend == "postgresql" else "JSON"
    id_type = "BIGSERIAL" if backend == "postgresql" else "INTEGER"
    timestamp_sql = "NOW()" if backend == "postgresql" else "CURRENT_TIMESTAMP"
    primary_key_suffix = " PRIMARY KEY" if backend == "postgresql" else " PRIMARY KEY AUTOINCREMENT"

    with engine.begin() as connection:
        connection.execute(
            sql_text(
                f"""
                CREATE TABLE IF NOT EXISTS incoming_file_events (
                    id {id_type}{primary_key_suffix},
                    event_type TEXT NOT NULL,
                    asset_key TEXT NOT NULL,
                    file_name TEXT,
                    incoming_dir TEXT,
                    processed_dir TEXT,
                    content TEXT,
                    metadata_json {metadata_type} NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT {timestamp_sql}
                )
                """
            )
        )
        insert_sql = (
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
            if backend == "postgresql"
            else """
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
                :metadata_json
            )
            """
        )
        connection.execute(
            sql_text(insert_sql),
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


def _write_local_event_snapshot(
    payload: Mapping[str, object],
    asset_key: str,
    values: Mapping[str, str],
) -> None:
    enabled = values.get("CDS_LOCAL_EVENT_LOG_ENABLED", "1").strip().lower()
    if enabled in {"0", "false", "no", "off"}:
        return

    output_path = Path(
        values.get(
            "CDS_LOCAL_EVENT_LOG_PATH",
            ".cache/cds/incoming_file_events.jsonl",
        )
    )

    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": str(payload.get("event", asset_key)),
        "asset_key": asset_key,
        "file_name": payload.get("file_name"),
        "status": payload.get("status"),
        "row_count": payload.get("row_count"),
        "metadata": dict(payload),
    }

    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=True) + "\n")
    except OSError:
        # Local event logging is best effort and must not block pipeline execution.
        return


def _has_backend_settings(prefix: str, values: Mapping[str, str]) -> bool:
    backend = values.get(f"{prefix}_BACKEND", "postgres").lower()
    if backend == "sqlite":
        return bool(values.get(f"{prefix}_SQLITE_PATH") or values.get(f"{prefix}_PATH"))
    return bool(values.get(f"{prefix}_HOST"))
