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

import dlt


@dlt.resource(name="ping")
def sample_source():
    """Trivial resource so the pipeline is runnable out of the box."""
    yield {"id": 1, "message": "hello from the cds dlt module"}


def main() -> None:
    pipeline = dlt.pipeline(
        pipeline_name=os.environ.get("DLT_PIPELINE_NAME", "cds_dlt_pipeline"),
        destination="postgres",
        dataset_name=os.environ.get("DLT_DATASET_NAME", "raw"),
    )
    load_info = pipeline.run(sample_source())
    print(load_info)


if __name__ == "__main__":
    main()
