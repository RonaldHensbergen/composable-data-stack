#!/bin/sh
set -eu

# Required connection settings, provided by the module's compose environment
# (sourced from whichever contract is consumed -- sql-database via
# ${bindings.*} for postgres, or file-database for duckdb -- see
# module.yaml's config.destinationType). module.yaml's requiredIf on each
# consume entry already enforces this match at compile time (E041), so a
# mismatch should never reach a running container; the checks below are
# defense in depth (e.g. against a hand-edited/legacy compose file) rather
# than the primary enforcement point. dlt's destination reads the
# DESTINATION__<NAME>__CREDENTIALS variable itself; see
# https://dlthub.com/docs/dlt-ecosystem/destinations/postgres and
# https://dlthub.com/docs/dlt-ecosystem/destinations/duckdb.
: "${CDS_DLT_DESTINATION:=postgres}"
: "${DLT_PROJECT_DIR:?DLT_PROJECT_DIR is required}"
: "${DLT_ENTRYPOINT:?DLT_ENTRYPOINT is required}"

case "$CDS_DLT_DESTINATION" in
    postgres)
        : "${DESTINATION__POSTGRES__CREDENTIALS:?DESTINATION__POSTGRES__CREDENTIALS is required when CDS_DLT_DESTINATION=postgres -- set config.destinationDatabase.contractRef}"
        ;;
    duckdb)
        : "${DESTINATION__DUCKDB__CREDENTIALS:?DESTINATION__DUCKDB__CREDENTIALS is required when CDS_DLT_DESTINATION=duckdb -- set config.destinationWarehouseFile.contractRef}"
        ;;
    *)
        echo "Unsupported CDS_DLT_DESTINATION '$CDS_DLT_DESTINATION' (expected postgres or duckdb)" >&2
        exit 1
        ;;
esac

cd "$DLT_PROJECT_DIR"
exec python "$DLT_ENTRYPOINT"
