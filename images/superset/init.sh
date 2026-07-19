#!/bin/bash
set -e

if [ -n "$SUPERSET__SQLALCHEMY_DATABASE_URI" ]; then
  echo "Running database migrations..."
  superset db upgrade
  echo "Database migrations completed"

  if [ -n "$SUPERSET_ADMIN_USERNAME" ] && [ -n "$SUPERSET_ADMIN_PASSWORD" ] && [ -n "$SUPERSET_ADMIN_EMAIL" ]; then
    echo "Creating admin user..."
    superset fab create-admin \
      --username "$SUPERSET_ADMIN_USERNAME" \
      --password "$SUPERSET_ADMIN_PASSWORD" \
      --email "$SUPERSET_ADMIN_EMAIL" \
      --firstname "Admin" \
      --lastname "User" || echo "Admin user already exists or creation failed"
    echo "Admin user setup completed"
  fi
fi
