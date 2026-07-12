import hashlib
import os
from pathlib import Path

from dagster import (
    Definitions,
    RunRequest,
    SensorEvaluationContext,
    SkipReason,
    asset,
    define_asset_job,
    job,
    op,
    sensor,
)

INCOMING_DATA_DIR = Path(os.getenv("CDS_INCOMING_DATA_DIR", "/app/data/cds/incoming"))
PROCESSED_DATA_DIR = Path(os.getenv("CDS_PROCESSED_DATA_DIR", "/app/data/cds/processed"))


@asset
def hello_cds() -> str:
    return "hello from cds"


hello_cds_job = define_asset_job("hello_cds_job", selection=["hello_cds"])


@op(config_schema={"incoming_dir": str, "processed_dir": str, "files": [str]})
def pickup_incoming_files(context) -> None:
    incoming_dir = Path(context.op_config["incoming_dir"])
    processed_dir = Path(context.op_config["processed_dir"])
    files = context.op_config["files"]

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

    context.log.info(
        "Picked up %s file(s) from %s into %s",
        moved_files,
        incoming_dir,
        processed_dir,
    )


@job
def pickup_incoming_files_job() -> None:
    pickup_incoming_files()


@sensor(job=pickup_incoming_files_job, minimum_interval_seconds=30)
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
                "pickup_incoming_files": {
                    "config": {
                        "incoming_dir": str(INCOMING_DATA_DIR),
                        "processed_dir": str(PROCESSED_DATA_DIR),
                        "files": file_names,
                    }
                }
            }
        },
        tags={"cds.sensor": "incoming-files"},
    )


defs = Definitions(
    assets=[hello_cds],
    jobs=[hello_cds_job, pickup_incoming_files_job],
    sensors=[incoming_files_sensor],
)
