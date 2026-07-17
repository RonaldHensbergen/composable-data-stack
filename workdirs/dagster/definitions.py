import hashlib
import importlib
import importlib.util
import csv
import io
import json
import os
import re
from pathlib import Path

import psycopg2
from psycopg2 import sql
from psycopg2.extras import execute_values

from dagster import (
    AssetObservation,
    Definitions,
    Field,
    MetadataValue,
    RunRequest,
    SensorEvaluationContext,
    SkipReason,
    asset,
    define_asset_job,
    job,
    op,
    sensor,
)

def _load_module_from_path(module_name: str, file_path: Path):
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        raise ModuleNotFoundError(f"Unable to load module from {file_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


try:
    _db_connection = importlib.import_module("shared.python.db.connection")
except ModuleNotFoundError:
    repo_root = Path(__file__).resolve().parents[2]
    shared_connection_path = repo_root / "shared" / "python" / "db" / "connection.py"
    if shared_connection_path.exists():
        _db_connection = _load_module_from_path("cds_shared_db_connection", shared_connection_path)
    else:
        local_connection_path = Path(__file__).resolve().with_name("postgres_connection.py")
        _db_connection = _load_module_from_path("cds_local_postgres_connection", local_connection_path)

insert_incoming_file_event = _db_connection.insert_incoming_file_event

INCOMING_DATA_DIR = Path(os.getenv("CDS_INCOMING_DATA_DIR", "/app/data/cds/incoming"))
PROCESSED_DATA_DIR = Path(os.getenv("CDS_PROCESSED_DATA_DIR", "/app/data/cds/processed"))
SUPPORTED_INGEST_EXTENSIONS = {".csv", ".json", ".ndjson"}
TEXT_FILE_ENCODINGS = ("utf-8", "utf-8-sig", "cp1252", "latin-1")


def _is_processable_file(file_path: Path) -> bool:
    if not file_path.is_file():
        return False
    if file_path.name.startswith("."):
        return False
    return file_path.suffix.lower() in SUPPORTED_INGEST_EXTENSIONS


def _sanitize_identifier(value: str, max_length: int = 63) -> str:
    sanitized = re.sub(r"[^a-zA-Z0-9_]", "_", value).strip("_").lower()
    if not sanitized:
        sanitized = "ingested"
    if sanitized[0].isdigit():
        sanitized = f"col_{sanitized}"
    return sanitized[:max_length]


def _derive_target_table(file_path: Path) -> str:
    prefix = os.getenv("CDS_TARGET_DB_TABLE_PREFIX", "incoming")
    stem = _sanitize_identifier(file_path.stem)
    return _sanitize_identifier(f"{prefix}_{stem}")


def _resolve_target_db_uri() -> str:
    explicit_uri = os.getenv("CDS_TARGET_DB_CONNECTION_URI")
    if explicit_uri:
        return explicit_uri

    analytics_name = os.getenv("CDS_ANALYTICS_DB_NAME")
    analytics_user = os.getenv("CDS_ANALYTICS_DB_USER")
    analytics_password = os.getenv("CDS_ANALYTICS_DB_PASSWORD")
    analytics_host = os.getenv("CDS_ANALYTICS_DB_HOST", os.getenv("DAGSTER_DB_HOST", "postgres"))
    analytics_port = os.getenv("CDS_ANALYTICS_DB_PORT", os.getenv("DAGSTER_DB_PORT", "5432"))

    if analytics_name and analytics_user and analytics_password:
        return (
            f"postgresql://{analytics_user}:{analytics_password}"
            f"@{analytics_host}:{analytics_port}/{analytics_name}"
        )

    dagster_uri = os.getenv("DAGSTER_DB_CONNECTION_URI")
    if dagster_uri:
        return dagster_uri

    raise RuntimeError(
        "No target database configuration found. Set CDS_TARGET_DB_CONNECTION_URI "
        "or CDS_ANALYTICS_DB_* environment variables."
    )


def _read_text_with_fallback(file_path: Path) -> str:
    payload = file_path.read_bytes()
    decode_errors: list[str] = []
    for encoding in TEXT_FILE_ENCODINGS:
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError as exc:
            decode_errors.append(f"{encoding}: {exc}")

    raise RuntimeError(
        "Unable to decode text file {path}. Tried encodings: {encodings}".format(
            path=file_path,
            encodings="; ".join(decode_errors),
        )
    )


def _ingest_csv_file(conn, file_path: Path, table_name: str, source_file: str) -> int:
    with io.StringIO(_read_text_with_fallback(file_path), newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            return 0

        raw_headers = [header or "column" for header in reader.fieldnames]
        column_names: list[str] = []
        used_names: set[str] = set()
        for index, header in enumerate(raw_headers, start=1):
            base_name = _sanitize_identifier(header) or f"column_{index}"
            candidate = base_name
            suffix = 1
            while candidate in used_names:
                suffix += 1
                candidate = _sanitize_identifier(f"{base_name}_{suffix}")
            used_names.add(candidate)
            column_names.append(candidate)

        with conn.cursor() as cursor:
            table_ident = sql.Identifier(table_name)
            col_defs = sql.SQL(", ").join(
                sql.SQL("{} text").format(sql.Identifier(col_name))
                for col_name in column_names
            )
            create_query = sql.SQL(
                "CREATE TABLE IF NOT EXISTS {} "
                "(source_file text NOT NULL, ingested_at timestamptz NOT NULL DEFAULT now(), {})"
            ).format(table_ident, col_defs)
            cursor.execute(create_query)

            rows = []
            for row in reader:
                rows.append([source_file, *[(row.get(raw) if row.get(raw) is not None else "") for raw in raw_headers]])

            if not rows:
                conn.commit()
                return 0

            insert_cols = ["source_file", *column_names]
            insert_ident_list = sql.SQL(", ").join(sql.Identifier(col_name) for col_name in insert_cols)
            insert_query = sql.SQL("INSERT INTO {} ({}) VALUES %s").format(table_ident, insert_ident_list)
            execute_values(cursor, insert_query, rows)

    conn.commit()
    return len(rows)


def _ingest_json_file(conn, file_path: Path, table_name: str, source_file: str) -> int:
    content = _read_text_with_fallback(file_path).strip()
    if not content:
        return 0

    records: list[dict] = []
    if file_path.suffix.lower() == ".ndjson":
        for line in content.splitlines():
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    else:
        parsed = json.loads(content)
        if isinstance(parsed, list):
            records.extend(parsed)
        else:
            records.append(parsed)

    with conn.cursor() as cursor:
        table_ident = sql.Identifier(table_name)
        cursor.execute(
            sql.SQL(
                "CREATE TABLE IF NOT EXISTS {} "
                "(source_file text NOT NULL, ingested_at timestamptz NOT NULL DEFAULT now(), payload jsonb NOT NULL)"
            ).format(table_ident)
        )

        if not records:
            conn.commit()
            return 0

        insert_query = sql.SQL("INSERT INTO {} (source_file, payload) VALUES %s").format(table_ident)
        rows = [(source_file, json.dumps(record)) for record in records]
        execute_values(cursor, insert_query, rows)

    conn.commit()
    return len(records)


def _ingest_file_into_db(conn, file_path: Path) -> tuple[str, int]:
    table_name = _derive_target_table(file_path)
    suffix = file_path.suffix.lower()
    if suffix == ".csv":
        return table_name, _ingest_csv_file(conn, file_path, table_name, file_path.name)
    if suffix in {".json", ".ndjson"}:
        return table_name, _ingest_json_file(conn, file_path, table_name, file_path.name)
    raise RuntimeError(
        f"Unsupported file extension '{suffix or '<none>'}'. "
        "Supported extensions are .csv, .json, and .ndjson"
    )


def save_data_to_db(context, payload: dict, asset_key: str = "cds_ingestion") -> None:
    """Persist payload to the analytics database and emit a Dagster event."""

    metadata = {}
    for key, value in payload.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            metadata[key] = value
        else:
            metadata[key] = MetadataValue.json(value)

    insert_incoming_file_event(payload, asset_key)

    context.log_event(AssetObservation(asset_key=asset_key, metadata=metadata))


def _move_files(context, incoming_dir: Path, processed_dir: Path, files: list[str]) -> tuple[int, int]:
    processed_dir.mkdir(parents=True, exist_ok=True)
    db_uri = _resolve_target_db_uri()

    moved_files = 0
    ingested_records = 0
    for file_name in files:
        source = incoming_dir / file_name
        if not _is_processable_file(source):
            continue

        try:
            with psycopg2.connect(db_uri) as conn:
                table_name, row_count = _ingest_file_into_db(conn, source)
                ingested_records += row_count
            context.log.info(
                "Ingested %s row(s) from %s into table %s",
                row_count,
                source.name,
                table_name,
            )
        except Exception as exc:  # noqa: BLE001
            context.log.exception("Failed to ingest file %s: %s", source, exc)
            continue

        destination = processed_dir / file_name
        if destination.exists():
            stem = destination.stem
            suffix = destination.suffix
            counter = 1
            while True:
                candidate = processed_dir / f"{stem}_{counter}{suffix}"
                if not candidate.exists():
                    destination = candidate
                    break
                counter += 1

        source.replace(destination)
        moved_files += 1

    return moved_files, ingested_records


@asset
def hello_cds() -> str:
    return "hello from cds"


hello_cds_job = define_asset_job("hello_cds_job", selection=["hello_cds"])


@op(config_schema={"incoming_dir": str, "processed_dir": str, "files": [str]})
def pickup_incoming_files(context) -> None:
    incoming_dir = Path(context.op_config["incoming_dir"])
    processed_dir = Path(context.op_config["processed_dir"])
    files = context.op_config["files"]

    moved_files, ingested_records = _move_files(context, incoming_dir, processed_dir, files)

    context.log.info(
        "Picked up %s file(s) from %s into %s after ingesting %s record(s)",
        moved_files,
        incoming_dir,
        processed_dir,
        ingested_records,
    )
    save_data_to_db(
        context,
        {
            "event": "pickup_incoming_files",
            "incoming_dir": str(incoming_dir),
            "processed_dir": str(processed_dir),
            "file_count": moved_files,
            "row_count": ingested_records,
            "files": files,
        },
    )


@op(config_schema={"incoming_dir": str, "file_name": Field(str, is_required=False)})
def read_data(context) -> dict:
    incoming_dir = Path(context.op_config["incoming_dir"])
    configured_file_name = context.op_config.get("file_name")

    if configured_file_name:
        target = incoming_dir / configured_file_name
        candidates = [target] if target.exists() and target.is_file() else []
    else:
        candidates = sorted(
            [entry for entry in incoming_dir.iterdir() if entry.is_file()]
            if incoming_dir.exists()
            else [],
            key=lambda entry: entry.name,
        )

    if not candidates:
        context.log.info("No incoming files available to read in %s", incoming_dir)
        return {"status": "no_file", "incoming_dir": str(incoming_dir)}

    selected = candidates[0]
    content = selected.read_text(encoding="utf-8", errors="replace")

    result = {
        "status": "ok",
        "incoming_dir": str(incoming_dir),
        "file_name": selected.name,
        "size_bytes": selected.stat().st_size,
        "content": content,
    }

    save_data_to_db(
        context,
        {
            "event": "read_data",
            "incoming_dir": str(incoming_dir),
            "file_name": selected.name,
            "size_bytes": selected.stat().st_size,
        },
        asset_key="cds_read",
    )

    return result


@op(config_schema={"processed_dir": str})
def process_incoming_file(context, read_result: dict) -> None:
    if read_result.get("status") != "ok":
        context.log.info("Skipping processing because no file was read")
        return

    incoming_dir = Path(read_result["incoming_dir"])
    processed_dir = Path(context.op_config["processed_dir"])
    file_name = read_result["file_name"]

    moved_files, ingested_records = _move_files(context, incoming_dir, processed_dir, [file_name])
    preview = read_result.get("content", "")[:200]

    save_data_to_db(
        context,
        {
            "event": "process_incoming_file",
            "incoming_dir": str(incoming_dir),
            "processed_dir": str(processed_dir),
            "file_name": file_name,
            "moved_count": moved_files,
            "row_count": ingested_records,
            "content": read_result.get("content"),
            "content_preview": preview,
        },
        asset_key="cds_pipeline",
    )


@job
def process_incoming_file_job() -> None:
    process_incoming_file(read_data())


@job
def pickup_incoming_files_job() -> None:
    pickup_incoming_files()


@sensor(job=process_incoming_file_job, minimum_interval_seconds=30)
def incoming_files_sensor(context: SensorEvaluationContext):
    if not INCOMING_DATA_DIR.exists():
        return SkipReason(f"Incoming directory does not exist: {INCOMING_DATA_DIR}")

    files = sorted(
        [entry for entry in INCOMING_DATA_DIR.iterdir() if _is_processable_file(entry)],
        key=lambda entry: entry.name,
    )
    if not files:
        return SkipReason(f"No files found in {INCOMING_DATA_DIR}")

    state_parts: list[str] = []
    file_names: list[str] = []
    for entry in files:
        file_stat = entry.stat()
        state_parts.append(f"{entry.name}:{file_stat.st_size}:{file_stat.st_mtime_ns}")
        file_names.append(entry.name)

    state_signature = "|".join(state_parts)
    if context.cursor == state_signature:
        return SkipReason("No new incoming files detected")

    context.update_cursor(state_signature)
    run_key = hashlib.sha256(state_signature.encode("utf-8")).hexdigest()

    return RunRequest(
        run_key=run_key,
        run_config={
            "ops": {
                "read_data": {
                    "config": {
                        "incoming_dir": str(INCOMING_DATA_DIR),
                        "file_name": file_names[0],
                    }
                },
                "process_incoming_file": {
                    "config": {
                        "processed_dir": str(PROCESSED_DATA_DIR),
                    }
                }
            }
        },
        tags={"cds.sensor": "incoming-files"},
    )


defs = Definitions(
    assets=[hello_cds],
    jobs=[hello_cds_job, pickup_incoming_files_job, process_incoming_file_job],
    sensors=[incoming_files_sensor],
)
