#!/bin/sh
set -eu

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
    postgres|sqlite)
        if [ "$BACKEND" != "$DAGSTER_IMAGE_DB_BACKEND" ]; then
            echo "Dagster backend '$BACKEND' does not match image backend '$DAGSTER_IMAGE_DB_BACKEND'" >&2
            exit 1
        fi
        ;;
    mysql)
        echo "MySQL storage is not supported by this Dagster image" >&2
        exit 1
        ;;
    *)
        echo "Unsupported Dagster database backend: $BACKEND" >&2
        exit 1
        ;;
esac

cp /app/images/dagster/workspace.yaml "$DAGSTER_HOME/workspace.yaml"
python /app/images/dagster/generate_config.py

if [ "$#" -ge 3 ] && [ "$1" = "dagster" ] && [ "$2" = "code-server" ] && [ "$3" = "start" ]; then
    rm -f /var/run/dagster/user-code.sock
fi

exec "$@"
