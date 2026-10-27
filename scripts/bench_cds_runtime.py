"""One-off benchmark for the cds-runtime read path and registry race (issue #986).

Manual tool, not a test: wall-clock numbers are machine-dependent, so this
script never runs in CI (no assertions). Run it against a live PostgreSQL:

    docker run -d --rm --name cds-bench-pg -e POSTGRES_PASSWORD=benchpass \
        -e POSTGRES_USER=bench -e POSTGRES_DB=bench \
        -p 127.0.0.1:55432:5432 postgres:16-alpine
    uv run --with sqlalchemy --with psycopg2-binary --python 3.14 \
        python scripts/bench_cds_runtime.py

Produces the numbers documented in docs/runtime-api.md (Limitations).
"""
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "runtime"))

from cds_runtime import connections  # noqa: E402

PG = {
    "CDS_DB_BENCH_BACKEND": "postgres",
    "CDS_DB_BENCH_HOST": "127.0.0.1",
    "CDS_DB_BENCH_PORT": "55432",
    "CDS_DB_BENCH_DATABASE": "bench",
    "CDS_DB_BENCH_USERNAME": "bench",
    "CDS_DB_BENCH_PASSWORD": "benchpass",
}
ROWS = (1, 10, 100)
WARMUP = 20
ITERATIONS = 200


def _timed(fn, n):
    samples = []
    for _ in range(n):
        started = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - started) * 1000.0)
    return statistics.median(samples), samples


def benchmark_read_path():
    import sqlalchemy

    db = connections.database("bench", env=PG)
    db.execute("DROP TABLE IF EXISTS bench_rows")
    db.execute("CREATE TABLE bench_rows (id INTEGER PRIMARY KEY, payload TEXT)")
    for count in ROWS:
        db.execute("DELETE FROM bench_rows")
        db.insert_rows(
            "bench_rows",
            [{"id": i, "payload": "x" * 40} for i in range(1, count + 1)],
        )

        statement = "SELECT id, payload FROM bench_rows"
        _timed(lambda s=statement: db.fetch_all(s), WARMUP)  # warm pools
        median_tx, _ = _timed(lambda s=statement: db.fetch_all(s), ITERATIONS)

        engine = sqlalchemy.create_engine(db.connection_uri)

        def raw_select(e=engine, s=statement):
            with e.connect() as connection:
                connection.exec_driver_sql(s).fetchall()

        _timed(raw_select, WARMUP)
        median_raw, _ = _timed(raw_select, ITERATIONS)
        overhead = (median_tx - median_raw) / median_raw * 100.0 if median_raw else 0.0
        print(
            f"fetch_all {count:3d} rows: {median_tx:6.2f}ms/op (cds-runtime, per-query BEGIN/COMMIT)"
            f"  vs {median_raw:6.2f}ms/op raw connect()  -> overhead {overhead:+5.1f}%"
        )
        engine.dispose()
    db.close()


def benchmark_registry_race():
    """Thundering herd on a cold registry: how many engines get created?"""
    import os

    import sqlalchemy

    created = []
    original_create_engine = sqlalchemy.create_engine

    def counting_create_engine(*args, **kwargs):
        engine = original_create_engine(*args, **kwargs)
        created.append(engine)
        return engine

    sqlalchemy.create_engine = counting_create_engine
    herd_env = {
        "CDS_DB_HERD_BACKEND": "postgres",
        "CDS_DB_HERD_HOST": "127.0.0.1",
        "CDS_DB_HERD_PORT": "55432",
        "CDS_DB_HERD_DATABASE": "bench",
        "CDS_DB_HERD_USERNAME": "bench",
        "CDS_DB_HERD_PASSWORD": "benchpass",
    }
    saved = {key: os.environ.get(key) for key in herd_env}
    try:
        os.environ.update(herd_env)
        connections.close_all()

        def worker():
            return connections.database("herd").fetch_all("SELECT 1 AS x")

        with ThreadPoolExecutor(max_workers=16) as pool:
            futures = [pool.submit(worker) for _ in range(64)]
            for future in futures:
                future.result()
    finally:
        sqlalchemy.create_engine = original_create_engine
        connections.close_all()
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    print(f"cold-registry thundering herd (16 threads x fetch): {len(created)} engine(s) created")
    return len(created)


if __name__ == "__main__":
    print(f"cds-runtime read-path benchmark (iterations={ITERATIONS}, median reported)")
    benchmark_read_path()
    print()
    benchmark_registry_race()
