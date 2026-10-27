"""Example workload: a read/write roundtrip through one named connection.

The same file runs unchanged in any CDS-managed Python runtime (issue
#987's acceptance criterion): the caller passes the connection name, and
the runtime's environment provides the `CDS_DB_<NAME>_*` variables the
named connection resolves. The dagster workdir runs it as an asset; the
dlt pipeline runs it as a pre-load destination check.
"""
import importlib.util
import sys
from pathlib import Path


def _load_cds_runtime():
    """Import cds_runtime from the runtime/ directory (image or repo root)."""
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


def roundtrip(name: str, marker: str) -> str:
    """Write and read back one marker row through the named connection."""
    connections = _load_cds_runtime().connections
    db = connections.database(name)
    db.execute(
        "CREATE TABLE IF NOT EXISTS cds_roundtrip (id INTEGER PRIMARY KEY, marker TEXT)"
    )
    db.execute("DELETE FROM cds_roundtrip WHERE id = :id", {"id": 1})
    db.execute(
        "INSERT INTO cds_roundtrip (id, marker) VALUES (:id, :marker)",
        {"id": 1, "marker": marker},
    )
    row = db.fetch_one("SELECT marker FROM cds_roundtrip WHERE id = :id", {"id": 1})
    return row["marker"] if row else ""


def _load_from_path(name: str, file_path: Path):
    spec = importlib.util.spec_from_file_location(name, file_path)
    if spec is None or spec.loader is None:
        raise ModuleNotFoundError(f"Unable to load module from {file_path}") from None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


if __name__ == "__main__":
    import os

    connection_name = os.environ.get("CDS_ROUNDTRIP_CONNECTION", "analytics")
    result = roundtrip(connection_name, os.environ.get("CDS_ROUNDTRIP_MARKER", "hello-cds"))
    print(f"cds-runtime roundtrip via {connection_name!r}: {result!r}")
