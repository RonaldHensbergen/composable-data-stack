import csv
import gzip
import hashlib
import os
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

import psycopg2

from dagster import MetadataValue, asset, define_asset_job

TENDER_SNAPSHOT_COLUMNS = (
    "publicatie_id",
    "source_url",
    "kenmerk",
    "aanbesteding_naam",
    "opdrachtgever_naam",
    "publicatie_datum",
    "type_publicatie",
    "type_opdracht",
    "procedure",
    "europees",
    "publicatiecode",
    "publicatiestatus",
    "source_ingested_at",
    "chunk_count",
    "has_pdf",
    "cpv_codes",
    "nuts_codes",
    "trefwoorden",
)

TENDER_SNAPSHOT_PATH = Path(
    os.getenv(
        "CDS_TENDER_SNAPSHOT_PATH",
        "/app/data/cds/incoming/tenderned-tenders.csv.gz",
    )
)

_CREATE_MODEL_SQL = """
CREATE SCHEMA IF NOT EXISTS tender_analytics;

CREATE TABLE IF NOT EXISTS tender_analytics.tenders (
    publicatie_id text PRIMARY KEY,
    source_url text,
    kenmerk text,
    aanbesteding_naam text,
    opdrachtgever_naam text,
    publicatie_datum date,
    type_publicatie text,
    type_opdracht text,
    procedure text,
    europees boolean,
    publicatiecode text,
    publicatiestatus text,
    source_ingested_at timestamptz,
    chunk_count integer NOT NULL CHECK (chunk_count > 0),
    has_pdf boolean NOT NULL,
    cpv_codes jsonb NOT NULL DEFAULT '[]'::jsonb,
    nuts_codes jsonb NOT NULL DEFAULT '[]'::jsonb,
    trefwoorden jsonb NOT NULL DEFAULT '[]'::jsonb,
    snapshot_loaded_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS tenders_publication_date_idx
    ON tender_analytics.tenders (publicatie_datum);
CREATE INDEX IF NOT EXISTS tenders_authority_idx
    ON tender_analytics.tenders (opdrachtgever_naam);
CREATE INDEX IF NOT EXISTS tenders_contract_type_idx
    ON tender_analytics.tenders (type_opdracht);
"""

_CREATE_STAGE_SQL = """
CREATE TEMP TABLE tender_snapshot_stage (
    publicatie_id text,
    source_url text,
    kenmerk text,
    aanbesteding_naam text,
    opdrachtgever_naam text,
    publicatie_datum text,
    type_publicatie text,
    type_opdracht text,
    procedure text,
    europees text,
    publicatiecode text,
    publicatiestatus text,
    source_ingested_at text,
    chunk_count text,
    has_pdf text,
    cpv_codes text,
    nuts_codes text,
    trefwoorden text
) ON COMMIT DROP;
"""

_COPY_STAGE_SQL = """
COPY tender_snapshot_stage (
    publicatie_id, source_url, kenmerk, aanbesteding_naam,
    opdrachtgever_naam, publicatie_datum, type_publicatie, type_opdracht,
    procedure, europees, publicatiecode, publicatiestatus,
    source_ingested_at, chunk_count, has_pdf, cpv_codes, nuts_codes,
    trefwoorden
) FROM STDIN WITH (FORMAT CSV, HEADER TRUE)
"""

_UPSERT_SQL = """
INSERT INTO tender_analytics.tenders (
    publicatie_id, source_url, kenmerk, aanbesteding_naam,
    opdrachtgever_naam, publicatie_datum, type_publicatie, type_opdracht,
    procedure, europees, publicatiecode, publicatiestatus,
    source_ingested_at, chunk_count, has_pdf, cpv_codes, nuts_codes,
    trefwoorden, snapshot_loaded_at
)
SELECT
    publicatie_id,
    NULLIF(source_url, ''),
    NULLIF(kenmerk, ''),
    NULLIF(aanbesteding_naam, ''),
    NULLIF(opdrachtgever_naam, ''),
    NULLIF(publicatie_datum, '')::date,
    NULLIF(type_publicatie, ''),
    NULLIF(type_opdracht, ''),
    NULLIF(procedure, ''),
    COALESCE(NULLIF(europees, '')::boolean, false),
    NULLIF(publicatiecode, ''),
    NULLIF(publicatiestatus, ''),
    NULLIF(source_ingested_at, '')::timestamptz,
    chunk_count::integer,
    has_pdf::boolean,
    COALESCE(NULLIF(cpv_codes, ''), '[]')::jsonb,
    COALESCE(NULLIF(nuts_codes, ''), '[]')::jsonb,
    COALESCE(NULLIF(trefwoorden, ''), '[]')::jsonb,
    now()
FROM tender_snapshot_stage
ON CONFLICT (publicatie_id) DO UPDATE SET
    source_url = EXCLUDED.source_url,
    kenmerk = EXCLUDED.kenmerk,
    aanbesteding_naam = EXCLUDED.aanbesteding_naam,
    opdrachtgever_naam = EXCLUDED.opdrachtgever_naam,
    publicatie_datum = EXCLUDED.publicatie_datum,
    type_publicatie = EXCLUDED.type_publicatie,
    type_opdracht = EXCLUDED.type_opdracht,
    procedure = EXCLUDED.procedure,
    europees = EXCLUDED.europees,
    publicatiecode = EXCLUDED.publicatiecode,
    publicatiestatus = EXCLUDED.publicatiestatus,
    source_ingested_at = EXCLUDED.source_ingested_at,
    chunk_count = EXCLUDED.chunk_count,
    has_pdf = EXCLUDED.has_pdf,
    cpv_codes = EXCLUDED.cpv_codes,
    nuts_codes = EXCLUDED.nuts_codes,
    trefwoorden = EXCLUDED.trefwoorden,
    snapshot_loaded_at = EXCLUDED.snapshot_loaded_at
WHERE (
    tenders.source_url,
    tenders.kenmerk,
    tenders.aanbesteding_naam,
    tenders.opdrachtgever_naam,
    tenders.publicatie_datum,
    tenders.type_publicatie,
    tenders.type_opdracht,
    tenders.procedure,
    tenders.europees,
    tenders.publicatiecode,
    tenders.publicatiestatus,
    tenders.source_ingested_at,
    tenders.chunk_count,
    tenders.has_pdf,
    tenders.cpv_codes,
    tenders.nuts_codes,
    tenders.trefwoorden
) IS DISTINCT FROM (
    EXCLUDED.source_url,
    EXCLUDED.kenmerk,
    EXCLUDED.aanbesteding_naam,
    EXCLUDED.opdrachtgever_naam,
    EXCLUDED.publicatie_datum,
    EXCLUDED.type_publicatie,
    EXCLUDED.type_opdracht,
    EXCLUDED.procedure,
    EXCLUDED.europees,
    EXCLUDED.publicatiecode,
    EXCLUDED.publicatiestatus,
    EXCLUDED.source_ingested_at,
    EXCLUDED.chunk_count,
    EXCLUDED.has_pdf,
    EXCLUDED.cpv_codes,
    EXCLUDED.nuts_codes,
    EXCLUDED.trefwoorden
);
"""

_CREATE_VIEW_SQL = """
CREATE OR REPLACE VIEW tender_analytics.tenders_dashboard AS
SELECT
    publicatie_id,
    source_url,
    kenmerk,
    aanbesteding_naam,
    opdrachtgever_naam,
    publicatie_datum,
    date_trunc('month', publicatie_datum)::date AS publicatie_maand,
    type_publicatie,
    type_opdracht,
    procedure,
    europees,
    publicatiecode,
    publicatiestatus,
    chunk_count,
    has_pdf,
    jsonb_array_length(cpv_codes) AS cpv_count,
    jsonb_array_length(nuts_codes) AS nuts_count,
    source_ingested_at,
    snapshot_loaded_at
FROM tender_analytics.tenders;
"""


@dataclass(frozen=True)
class TenderLoadResult:
    snapshot_rows: int
    warehouse_rows: int
    changed_rows: int
    earliest_publication: date | None
    latest_publication: date | None
    snapshot_sha256: str


def validate_tender_snapshot(path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"Tender snapshot not found: {path}")
    with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        header = tuple(next(reader, ()))
    if header != TENDER_SNAPSHOT_COLUMNS:
        raise RuntimeError(
            "Tender snapshot columns do not match the expected contract: "
            f"expected {TENDER_SNAPSHOT_COLUMNS}, got {header}"
        )


def snapshot_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_tender_snapshot(connection, snapshot_path: Path) -> TenderLoadResult:
    validate_tender_snapshot(snapshot_path)
    digest = snapshot_sha256(snapshot_path)

    with connection.cursor() as cursor:
        cursor.execute(_CREATE_MODEL_SQL)
        cursor.execute(_CREATE_STAGE_SQL)
        with gzip.open(snapshot_path, "rt", encoding="utf-8", newline="") as handle:
            cursor.copy_expert(_COPY_STAGE_SQL, handle)

        cursor.execute(
            """
            SELECT COUNT(*), COUNT(DISTINCT publicatie_id)
            FROM tender_snapshot_stage
            """
        )
        snapshot_rows, distinct_ids = cursor.fetchone()
        if snapshot_rows == 0:
            raise RuntimeError("Tender snapshot contains no rows")
        if snapshot_rows != distinct_ids:
            raise RuntimeError(
                "Tender snapshot contains duplicate publicatie_id values: "
                f"rows={snapshot_rows}, distinct={distinct_ids}"
            )

        cursor.execute(_UPSERT_SQL)
        changed_rows = cursor.rowcount
        cursor.execute(_CREATE_VIEW_SQL)
        cursor.execute(
            """
            SELECT COUNT(*), MIN(publicatie_datum), MAX(publicatie_datum)
            FROM tender_analytics.tenders
            """
        )
        warehouse_rows, earliest, latest = cursor.fetchone()

    return TenderLoadResult(
        snapshot_rows=snapshot_rows,
        warehouse_rows=warehouse_rows,
        changed_rows=changed_rows,
        earliest_publication=earliest,
        latest_publication=latest,
        snapshot_sha256=digest,
    )


@asset(group_name="tender_analytics", compute_kind="PostgreSQL")
def tender_analytics(context) -> dict[str, object]:
    """Load the read-only TenderNed metadata snapshot into local analytics."""
    validate_tender_snapshot(TENDER_SNAPSHOT_PATH)
    context.log.info("Loading tender snapshot from %s", TENDER_SNAPSHOT_PATH)
    with psycopg2.connect(os.environ["CDS_ANALYTICS_DB_CONNECTION_URI"]) as connection:
        result = load_tender_snapshot(connection, TENDER_SNAPSHOT_PATH)

    metadata = asdict(result)
    metadata["earliest_publication"] = (
        result.earliest_publication.isoformat() if result.earliest_publication else None
    )
    metadata["latest_publication"] = (
        result.latest_publication.isoformat() if result.latest_publication else None
    )
    context.add_output_metadata(
        {
            **metadata,
            "snapshot_path": MetadataValue.path(str(TENDER_SNAPSHOT_PATH)),
            "warehouse_table": "tender_analytics.tenders",
            "dashboard_view": "tender_analytics.tenders_dashboard",
        }
    )
    return metadata


load_tender_analytics_job = define_asset_job(
    "load_tender_analytics_job",
    selection=["tender_analytics"],
)
