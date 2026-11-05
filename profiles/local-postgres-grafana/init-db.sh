#!/bin/bash
set -e

psql -v ON_ERROR_STOP=1 \
     -U "$POSTGRES_USER" \
     -d "$POSTGRES_DB" \
           -v analytics_password="$ANALYTICS_DB_PASSWORD" \
           -v analytics_db="$ANALYTICS_DB_NAME" \
           -v analytics_user="$ANALYTICS_DB_USER" <<'SQL'
-- Create the analytics user if it doesn't exist
SELECT format('CREATE USER %I WITH PASSWORD %L', :'analytics_user', :'analytics_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'analytics_user')
\gexec

-- Create the analytics database if it doesn't exist
SELECT format('CREATE DATABASE %I', :'analytics_db')
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = :'analytics_db')
\gexec

-- Grant privileges on the analytics database
SELECT format('GRANT ALL PRIVILEGES ON DATABASE %I TO %I', :'analytics_db', :'analytics_user')
\gexec

\connect :analytics_db
GRANT ALL ON SCHEMA public TO :analytics_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO :analytics_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO :analytics_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON FUNCTIONS TO :analytics_user;
SQL
