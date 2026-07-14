import hashlib
import importlib
import os
from pathlib import Path

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

try:
    _postgres_connection = importlib.import_module("workdirs.dagster.postgres_connection")
except ModuleNotFoundError:
    _postgres_connection = importlib.import_module("postgres_connection")

insert_incoming_file_event = _postgres_connection.insert_incoming_file_event

INCOMING_DATA_DIR = Path(os.getenv("CDS_INCOMING_DATA_DIR", "/app/data/cds/incoming"))
PROCESSED_DATA_DIR = Path(os.getenv("CDS_PROCESSED_DATA_DIR", "/app/data/cds/processed"))


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


def _move_files(incoming_dir: Path, processed_dir: Path, files: list[str]) -> int:
    processed_dir.mkdir(parents=True, exist_ok=True)

    moved_files = 0
    for file_name in files:
        source = incoming_dir / file_name
        if not source.exists() or not source.is_file():
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

    return moved_files


@asset
def hello_cds() -> str:
    return "hello from cds"


hello_cds_job = define_asset_job("hello_cds_job", selection=["hello_cds"])


@op(config_schema={"incoming_dir": str, "processed_dir": str, "files": [str]})
def pickup_incoming_files(context) -> None:
    incoming_dir = Path(context.op_config["incoming_dir"])
    processed_dir = Path(context.op_config["processed_dir"])
    files = context.op_config["files"]

    moved_files = _move_files(incoming_dir, processed_dir, files)

    context.log.info(
        "Picked up %s file(s) from %s into %s",
        moved_files,
        incoming_dir,
        processed_dir,
    )
    save_data_to_db(
        context,
        {
            "event": "pickup_incoming_files",
            "incoming_dir": str(incoming_dir),
            "processed_dir": str(processed_dir),
            "file_count": moved_files,
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

    moved_files = _move_files(incoming_dir, processed_dir, [file_name])
    preview = read_result.get("content", "")[:200]

    save_data_to_db(
        context,
        {
            "event": "process_incoming_file",
            "incoming_dir": str(incoming_dir),
            "processed_dir": str(processed_dir),
            "file_name": file_name,
            "moved_count": moved_files,
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
        [entry for entry in INCOMING_DATA_DIR.iterdir() if entry.is_file()],
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
