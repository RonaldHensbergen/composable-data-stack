"""Minimal example dlt pipeline for the CDS `dlt` ingestion module.

Replace `sample_source` below with a real extractor (a REST API, files, a
source database, etc.) -- this only demonstrates that the pipeline can
connect to and load into the destination configured by the module's
consumed contract: a sql-database contract (postgres) or a file-database
contract (duckdb), selected by config.destinationType.

CDS_DLT_DESTINATION, DESTINATION__POSTGRES__CREDENTIALS (postgres mode),
DESTINATION__DUCKDB__CREDENTIALS (duckdb mode), DLT_PIPELINE_NAME, and
DLT_DATASET_NAME are set by the dlt module's compose environment (see
modules-experimental/ingestion/dlt/module.yaml and images/dlt/entrypoint.sh);
dlt itself reads the DESTINATION__<NAME>__CREDENTIALS variable directly,
this script only needs to read the destination type and pipeline/dataset
names.
"""
import os
from pathlib import Path

import dlt


def _postgres_roundtrip_check() -> str:
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


def _duckdb_roundtrip_check() -> str:
    """Write/read one marker row through the shared DuckDB file directly.

    DuckDB is embedded (no connection URI): the destination is the database
    file bind-mounted from the consumed file-database contract, so this
    check opens it with the duckdb client library shipped by dlt[duckdb] and
    proves the bind mount works before dlt loads into it. The connection is
    closed again afterwards -- DuckDB allows only one read-write connection
    per file at a time, and dlt opens its own when the pipeline runs.
    """
    import duckdb

    path = os.environ["DESTINATION__DUCKDB__CREDENTIALS"]
    con = duckdb.connect(path)
    try:
        con.execute(
            "CREATE TABLE IF NOT EXISTS cds_roundtrip (id INTEGER PRIMARY KEY, marker TEXT)"
        )
        con.execute("DELETE FROM cds_roundtrip WHERE id = 1")
        con.execute(
            "INSERT INTO cds_roundtrip (id, marker) VALUES (1, 'hello from the dlt runtime')"
        )
        row = con.execute("SELECT marker FROM cds_roundtrip WHERE id = 1").fetchone()
    finally:
        con.close()
    return row[0] if row else ""


@dlt.resource(name="ping")
def sample_source():
    """Trivial resource so the pipeline is runnable out of the box."""
    yield {"id": 1, "message": "hello from the cds dlt module"}


def main() -> None:
    destination = os.environ.get("CDS_DLT_DESTINATION", "postgres")
    if destination == "duckdb":
        print(f"destination roundtrip: {_duckdb_roundtrip_check()!r}")
    else:
        print(f"destination roundtrip: {_postgres_roundtrip_check()!r}")
    pipeline = dlt.pipeline(
        pipeline_name=os.environ.get("DLT_PIPELINE_NAME", "cds_dlt_pipeline"),
        destination=destination,
        dataset_name=os.environ.get("DLT_DATASET_NAME", "raw"),
    )
    load_info = pipeline.run(sample_source())
    print(load_info)


if __name__ == "__main__":
    main()
