#!/bin/sh
set -e
python /app/images/dagster/generate_config.py
exec "$@"
