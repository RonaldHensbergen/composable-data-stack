#!/bin/bash
set -e

# Run database migrations if SUPERSET__SQLALCHEMY_DATABASE_URI is set
if [ -n "$SUPERSET__SQLALCHEMY_DATABASE_URI" ]; then
  echo "Running database migrations..."
  superset db upgrade
  echo "Database migrations completed"
  
  # Create admin user if it doesn't exist
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

# Start Superset
exec superset run -p 8088 --host 0.0.0.0
