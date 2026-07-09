#!/bin/sh
set -e

BACKEND="${DB_BACKEND:-}"
if [ -z "$BACKEND" ]; then
    URI="${DAGSTER_DB_CONNECTION_URI:-}"
    case "$URI" in
        postgresql*) BACKEND="postgres" ;;
        mysql*)      BACKEND="mysql" ;;
        sqlite*)     BACKEND="sqlite" ;;
        *)           BACKEND="postgres" ;;
    esac
fi

case "$BACKEND" in
    postgres) pip install --quiet --no-cache-dir dagster-postgres==0.29.12 ;;
    mysql)    pip install --quiet --no-cache-dir dagster-mysql==0.29.12 ;;
    sqlite)   ;; # built into dagster core
esac

python /app/images/dagster/generate_config.py
exec "$@"
