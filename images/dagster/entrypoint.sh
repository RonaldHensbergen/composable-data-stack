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
    sqlite)
        # Sqlite run/event/schedule storage ships in dagster core, so it works
        # regardless of which backend this image was built for. Default the
        # storage directory so sqlite mode needs zero extra configuration.
        if [ -z "${DAGSTER_SQLITE_DIR:-}" ]; then
            export DAGSTER_SQLITE_DIR="$DAGSTER_HOME/storage"
        fi
        mkdir -p "$DAGSTER_SQLITE_DIR"
        ;;
    postgres)
        if [ "$DAGSTER_IMAGE_DB_BACKEND" != "postgres" ]; then
            echo "Dagster backend 'postgres' requires an image built with DB_BACKEND=postgres (this image was built for '$DAGSTER_IMAGE_DB_BACKEND')" >&2
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

# The image source is mode 0444. Replace through a writable temporary file so
# repeated starts remain safe when DAGSTER_HOME lives on a persistent volume.
workspace_tmp="$DAGSTER_HOME/.workspace.yaml.tmp"
rm -f "$workspace_tmp"
cp /app/images/dagster/workspace.yaml "$workspace_tmp"
chmod 0644 "$workspace_tmp"
mv -f "$workspace_tmp" "$DAGSTER_HOME/workspace.yaml"
python /app/images/dagster/generate_config.py

if [ "$#" -ge 3 ] && [ "$1" = "dagster" ] && [ "$2" = "code-server" ] && [ "$3" = "start" ]; then
    rm -f /var/run/dagster/user-code.sock
fi

exec "$@"
