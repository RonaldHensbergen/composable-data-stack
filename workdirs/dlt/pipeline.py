"""Minimal example dlt pipeline for the CDS `dlt` ingestion module.

Replace `sample_source` below with a real extractor (a REST API, files, a
source database, etc.) -- this only demonstrates that the pipeline can
connect to and load into the destination configured by the module's
consumed sql-database contract.

DESTINATION__POSTGRES__CREDENTIALS, DLT_PIPELINE_NAME, and DLT_DATASET_NAME
are set by the dlt module's compose environment (see
modules-experimental/ingestion/dlt/module.yaml and images/dlt/entrypoint.sh);
dlt itself reads DESTINATION__POSTGRES__CREDENTIALS directly, this script
only needs to read the pipeline/dataset names.
"""
import os
from pathlib import Path

import dlt


def _destination_roundtrip_check() -> str:
    """Run the shared example workload against the destination connection.

    The same workdirs/db_roundtrip.py runs unchanged in the dagster
    runtime (as the `db_roundtrip` asset); this is the dlt-runtime half
    of that shared workload, resolving the canonical `destination`
    connection from the environment.
    """
    import importlib.util

    roundtrip_path = Path(__file__).resolve().parents[1] / "db_roundtrip.py"
    spec = importlib.util.spec_from_file_location("cds_db_roundtrip", roundtrip_path)
    if spec is None or spec.loader is None:
        raise ModuleNotFoundError(f"Unable to load module from {roundtrip_path}") from None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.roundtrip("destination", "hello from the dlt runtime")


@dlt.resource(name="ping")
def sample_source():
    """Trivial resource so the pipeline is runnable out of the box."""
    yield {"id": 1, "message": "hello from the cds dlt module"}


def main() -> None:
    print(f"destination roundtrip: {_destination_roundtrip_check()!r}")
    pipeline = dlt.pipeline(
        pipeline_name=os.environ.get("DLT_PIPELINE_NAME", "cds_dlt_pipeline"),
        destination="postgres",
        dataset_name=os.environ.get("DLT_DATASET_NAME", "raw"),
    )
    load_info = pipeline.run(sample_source())
    print(load_info)


if __name__ == "__main__":
    main()
