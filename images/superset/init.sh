#!/bin/bash
set -e

# Superset's web process may start concurrently (for example after a restart
# or an out-of-order start) and race this script creating the metadata tables.
# These steps are idempotent, so retry a bounded number of times.
retry() {
  local attempts=${INIT_RETRY_ATTEMPTS:-5} delay=${INIT_RETRY_DELAY:-5} n=1
  until "$@"; do
    if (( n >= attempts )); then
      echo "Command failed after ${attempts} attempts: $*" >&2
      return 1
    fi
    echo "Attempt ${n}/${attempts} failed; retrying in ${delay}s: $*" >&2
    n=$((n + 1))
    sleep "$delay"
  done
}

if [[ -n "$SUPERSET__SQLALCHEMY_DATABASE_URI" ]]; then
  echo "Running database migrations..."
  retry superset db upgrade
  echo "Database migrations completed"

  if [[ -n "$SUPERSET_ADMIN_USERNAME" && -n "$SUPERSET_ADMIN_PASSWORD" && -n "$SUPERSET_ADMIN_EMAIL" ]]; then
    echo "Creating admin user..."
    superset fab create-admin \
      --username "$SUPERSET_ADMIN_USERNAME" \
      --password "$SUPERSET_ADMIN_PASSWORD" \
      --email "$SUPERSET_ADMIN_EMAIL" \
      --firstname "Admin" \
      --lastname "User" || echo "Admin user already exists or creation failed"
    echo "Admin user setup completed"
  fi

  echo "Synchronizing roles and permissions..."
  retry superset init
  echo "Roles and permissions synchronized"
fi
