"""Postgres access for the dagster workdir, built on cds-runtime (issue #987).

This module is the migrated replacement for the legacy
``shared/python/db/connection.py`` helper: the connection plumbing lives
in the installable ``cds_runtime`` package (see docs/runtime-api.md), and
only the dagster-specific event persistence stayed behind in this
workdir. The legacy helper remains available in the images for
compatibility but is deprecated.
"""
import json
import sys
from datetime import UTC, datetime
from pathlib import Path


def _load_cds_runtime():
    """Import cds_runtime from the runtime/ directory.

    In the dagster image the package is baked in at /app/runtime
    (this file lives at /app/workdirs/dagster/); locally the repo root's
    runtime/ directory is used.
    """
    try:
        import cds_runtime  # noqa: F401
    except ModuleNotFoundError:
        for candidate in (
            Path(__file__).resolve().parents[2] / "runtime",
            Path("/app/runtime"),
        ):
            if (candidate / "cds_runtime").is_dir():
                sys.path.insert(0, str(candidate))
                break
        import cds_runtime  # noqa: F401
    return cds_runtime


_cds_runtime = _load_cds_runtime()
connections = _cds_runtime.connections

#: The dagster module ships a legacy `CDS_ANALYTICS_DB_*` environment
#: block; cds-runtime's prefix adoption reads it as-is, including the
#: legacy `_NAME`/`_USER` field spellings.
ANALYTICS_PREFIX = "CDS_ANALYTICS_DB"


def analytics():
    """The named `analytics` database connection (cds-runtime)."""
    return connections.database("analytics", prefix=ANALYTICS_PREFIX)


def get_postgres_connection_uri() -> str:
    """The analytics connection URI (compat surface for workload code)."""
    return analytics().connection_uri


def get_postgres_engine():
    """The analytics SQLAlchemy engine (compat surface for workload code)."""
    return analytics().engine()


def get_sqlalchemy_module():
    import sqlalchemy

    return sqlalchemy


def sql_text(statement: str):
    import sqlalchemy

    return sqlalchemy.text(statement)


def _is_postgres_uri(connection_uri: str) -> bool:
    """True for any postgres/postgresql scheme, driver suffix included.

    sqlalchemy accepts postgresql+<driver> URLs (psycopg2, psycopg, ...),
    so match on the scheme prefix rather than a fixed set of spellings.
    """
    return connection_uri.split(":", 1)[0].lower().startswith("postgres")


def _write_local_event_snapshot(payload: dict, asset_key: str, env) -> None:
    enabled = env.get("CDS_LOCAL_EVENT_LOG_ENABLED", "1").strip().lower()
    if enabled in {"0", "false", "no", "off"}:
        return

    output_path = Path(
        env.get(
            "CDS_LOCAL_EVENT_LOG_PATH",
            ".cache/cds/incoming_file_events.jsonl",
        )
    )
    entry = {
        "timestamp": datetime.now(UTC).isoformat(),
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


def insert_incoming_file_event(
    payload: dict,
    asset_key: str,
    env=None,
) -> None:
    """Persist one ingestion event row, through cds-runtime.

    Writes the local best-effort snapshot first; when the analytics
    connection is configured, also inserts into the
    ``incoming_file_events`` table (dialect-aware: JSONB metadata on
    PostgreSQL, JSON elsewhere).
    """
    import os

    values = os.environ if env is None else env
    _write_local_event_snapshot(payload, asset_key, values)

    if not values.get(f"{ANALYTICS_PREFIX}_CONNECTION_URI") and not (
        values.get(f"{ANALYTICS_PREFIX}_HOST")
        or values.get(f"{ANALYTICS_PREFIX}_SQLITE_PATH")
    ):
        return
    db = connections.database("analytics", prefix=ANALYTICS_PREFIX, env=values)

    is_postgres = _is_postgres_uri(db.connection_uri)
    metadata_type = "JSONB" if is_postgres else "JSON"
    id_type = "BIGSERIAL" if is_postgres else "INTEGER"
    timestamp_sql = "NOW()" if is_postgres else "CURRENT_TIMESTAMP"
    primary_key_suffix = " PRIMARY KEY" if is_postgres else " PRIMARY KEY AUTOINCREMENT"

    metadata_json = json.dumps(dict(payload))
    if is_postgres:
        insert_sql = """
            INSERT INTO incoming_file_events (
                event_type, asset_key, file_name, incoming_dir, processed_dir,
                content, metadata_json
            ) VALUES (
                :event_type, :asset_key, :file_name, :incoming_dir, :processed_dir,
                :content, CAST(:metadata_json AS JSONB)
            )
        """
    else:
        insert_sql = """
            INSERT INTO incoming_file_events (
                event_type, asset_key, file_name, incoming_dir, processed_dir,
                content, metadata_json
            ) VALUES (
                :event_type, :asset_key, :file_name, :incoming_dir, :processed_dir,
                :content, :metadata_json
            )
        """

    with db.transaction() as tx:
        tx.execute(
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
        tx.execute(
            insert_sql,
            {
                "event_type": str(payload.get("event", asset_key)),
                "asset_key": asset_key,
                "file_name": payload.get("file_name"),
                "incoming_dir": payload.get("incoming_dir"),
                "processed_dir": payload.get("processed_dir"),
                "content": payload.get("content"),
                "metadata_json": metadata_json,
            },
        )
    # One-shot writer: dispose the engine so a pooled sqlite connection
    # cannot hold the database file open after the event lands.
    db.close()


__all__ = [
    "analytics",
    "connections",
    "get_postgres_connection_uri",
    "get_postgres_engine",
    "get_sqlalchemy_module",
    "insert_incoming_file_event",
    "sql_text",
]
