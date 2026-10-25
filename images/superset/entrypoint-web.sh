#!/bin/bash
set -e

# Web serving mode (issue #216): production serves through gunicorn with
# worker/timeout settings from the module's webServer config; development
# uses Superset's built-in development server.
MODE="${CDS_WEBSERVER_MODE:-production}"
PORT="${CDS_WEBSERVER_PORT:-8088}"

if [ "$MODE" = "development" ]; then
    exec superset run -p "$PORT" --host 0.0.0.0
fi

WORKERS="${CDS_WEBSERVER_WORKERS:-4}"
TIMEOUT="${CDS_WEBSERVER_TIMEOUT:-60}"

GUNICORN="$(command -v gunicorn || true)"
if [ -z "$GUNICORN" ] && [ -x /app/.venv/bin/gunicorn ]; then
    GUNICORN="/app/.venv/bin/gunicorn"
fi
if [ -z "$GUNICORN" ]; then
    echo "ERROR: CDS_WEBSERVER_MODE=$MODE requires gunicorn, which is not present in this image." >&2
    echo "Use a Superset image built from images/superset/base/Dockerfile, or set webServer.mode=development." >&2
    exit 1
fi

# gunicorn: workers recycle on timeout, SIGTERM drains requests gracefully.
# --preload loads the app once in the master before forking, so concurrent
# workers cannot race each other (or superset-init) creating the metadata
# schema when the web process starts before the init container, e.g. after
# an out-of-order start or a restart against an empty database.
exec "$GUNICORN" \
    --bind "0.0.0.0:$PORT" \
    --preload \
    --workers "$WORKERS" \
    --worker-class gthread \
    --timeout "$TIMEOUT" \
    --graceful-timeout "$TIMEOUT" \
    --access-logfile - \
    --error-logfile - \
    "superset.app:create_app()"
