#!/bin/sh
set -eu

# Required by the module's compose environment (sourced from the consumed
# sql-database contract via ${bindings.*}). dlt's postgres destination reads
# this exact variable name itself; see
# https://dlthub.com/docs/dlt-ecosystem/destinations/postgres.
: "${DESTINATION__POSTGRES__CREDENTIALS:?DESTINATION__POSTGRES__CREDENTIALS is required}"
: "${DLT_PROJECT_DIR:?DLT_PROJECT_DIR is required}"
: "${DLT_ENTRYPOINT:?DLT_ENTRYPOINT is required}"

cd "$DLT_PROJECT_DIR"
exec python "$DLT_ENTRYPOINT"
