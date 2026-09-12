#!/usr/bin/env bash
# Export tender-level metadata from the TenderNed pgvector source without writes.
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
OUTPUT="${1:-${REPO_ROOT}/data/tenderned/tenderned-tenders.csv.gz}"
SOURCE_CONTEXT="${CDS_TENDER_SOURCE_CONTEXT:-big-hetzner}"
SOURCE_NAMESPACE="${CDS_TENDER_SOURCE_NAMESPACE:-data-plane}"
SOURCE_POD="${CDS_TENDER_SOURCE_POD:-pgvector-0}"

mkdir -p "$(dirname "$OUTPUT")"
TEMP_OUTPUT="$(mktemp "${OUTPUT}.tmp.XXXXXX")"
cleanup() {
  rm -f "$TEMP_OUTPUT"
}
trap cleanup EXIT

echo "==> checking read-only TenderNed source access"
kubectl --context "$SOURCE_CONTEXT" get --raw=/readyz >/dev/null

echo "==> exporting one analytical row per tender"
kubectl --context "$SOURCE_CONTEXT" --namespace "$SOURCE_NAMESPACE" \
  exec -i "$SOURCE_POD" -- sh -ceu '
    export PGPASSWORD="$POSTGRES_PASSWORD"
    export PGOPTIONS="-c default_transaction_read_only=on -c statement_timeout=120000"
    exec psql -X -q -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"
  ' <<'SQL' | gzip -9 > "$TEMP_OUTPUT"
COPY (
    WITH tender_metadata AS (
        SELECT
            publicatie_id,
            COALESCE(
                MAX(source_url) FILTER (WHERE source = 'publication'),
                MAX(source_url)
            ) AS source_url,
            MAX(kenmerk) FILTER (WHERE source = 'publication') AS kenmerk,
            MAX(aanbesteding_naam) FILTER (WHERE source = 'publication') AS aanbesteding_naam,
            MAX(opdrachtgever_naam) FILTER (WHERE source = 'publication') AS opdrachtgever_naam,
            MAX(publicatie_datum) FILTER (WHERE source = 'publication') AS publicatie_datum,
            MAX(type_publicatie) FILTER (WHERE source = 'publication') AS type_publicatie,
            MAX(type_opdracht) FILTER (WHERE source = 'publication') AS type_opdracht,
            MAX(procedure) FILTER (WHERE source = 'publication') AS procedure,
            BOOL_OR(europees) FILTER (WHERE source = 'publication') AS europees,
            MAX(publicatiecode) FILTER (WHERE source = 'publication') AS publicatiecode,
            MAX(publicatiestatus) FILTER (WHERE source = 'publication') AS publicatiestatus,
            MAX(ingested_at) AS source_ingested_at,
            COUNT(*)::integer AS chunk_count,
            BOOL_OR(source = 'pdf') AS has_pdf
        FROM public.tender_chunks
        GROUP BY publicatie_id
    )
    SELECT
        metadata.publicatie_id,
        metadata.source_url,
        metadata.kenmerk,
        metadata.aanbesteding_naam,
        metadata.opdrachtgever_naam,
        metadata.publicatie_datum,
        metadata.type_publicatie,
        metadata.type_opdracht,
        metadata.procedure,
        COALESCE(metadata.europees, false) AS europees,
        metadata.publicatiecode,
        metadata.publicatiestatus,
        metadata.source_ingested_at,
        metadata.chunk_count,
        metadata.has_pdf OR details.pdf_path IS NOT NULL AS has_pdf,
        COALESCE(to_json(details.cpv_codes)::text, '[]') AS cpv_codes,
        COALESCE(to_json(details.nuts_codes)::text, '[]') AS nuts_codes,
        COALESCE(to_json(details.trefwoorden)::text, '[]') AS trefwoorden
    FROM tender_metadata AS metadata
    LEFT JOIN public.tender_details AS details USING (publicatie_id)
    ORDER BY metadata.publicatie_id
) TO STDOUT WITH (FORMAT CSV, HEADER TRUE);
SQL

gzip -t "$TEMP_OUTPUT"
ROW_COUNT="$(python3 - "$TEMP_OUTPUT" <<'PY'
import gzip
import sys

with gzip.open(sys.argv[1], "rt", encoding="utf-8", newline="") as handle:
    print(max(0, sum(1 for _ in handle) - 1))
PY
)"
if [[ "$ROW_COUNT" -lt 1 ]]; then
  echo "ERROR TenderNed export returned no rows." >&2
  exit 1
fi

mv "$TEMP_OUTPUT" "$OUTPUT"
trap - EXIT
CHECKSUM="$(shasum -a 256 "$OUTPUT" | awk '{print $1}')"
printf 'snapshot=%s\nrows=%s\nsha256=%s\n' "$OUTPUT" "$ROW_COUNT" "$CHECKSUM"
