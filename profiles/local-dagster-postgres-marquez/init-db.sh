#!/bin/bash
set -e

psql -v ON_ERROR_STOP=1 \
     -U "$POSTGRES_USER" \
     -d "$POSTGRES_DB" \
           -v dagster_password="$DAGSTER_DB_PASSWORD" \
           -v marquez_password="$MARQUEZ_DB_PASSWORD" \
           -v analytics_password="$ANALYTICS_DB_PASSWORD" \
           -v analytics_db="$ANALYTICS_DB_NAME" \
           -v analytics_user="$ANALYTICS_DB_USER" \
           -v dagster_db="$DAGSTER_DB_NAME" \
           -v dagster_user="$DAGSTER_DB_USER" \
           -v marquez_db="$MARQUEZ_DB_NAME" \
           -v marquez_user="$MARQUEZ_DB_USER" <<'SQL'
-- Create users if they don't exist
SELECT format('CREATE USER %I WITH PASSWORD %L', :'analytics_user', :'analytics_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'analytics_user')
\gexec

SELECT format('CREATE USER %I WITH PASSWORD %L', :'dagster_user', :'dagster_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'dagster_user')
\gexec

SELECT format('CREATE USER %I WITH PASSWORD %L', :'marquez_user', :'marquez_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'marquez_user')
\gexec

-- Create databases if they don't exist
SELECT format('CREATE DATABASE %I', :'analytics_db')
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = :'analytics_db')
\gexec

SELECT format('CREATE DATABASE %I', :'dagster_db')
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = :'dagster_db')
\gexec

SELECT format('CREATE DATABASE %I', :'marquez_db')
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = :'marquez_db')
\gexec

-- Grant privileges on databases
SELECT format('GRANT ALL PRIVILEGES ON DATABASE %I TO %I', :'analytics_db', :'analytics_user')
\gexec

SELECT format('GRANT ALL PRIVILEGES ON DATABASE %I TO %I', :'dagster_db', :'dagster_user')
\gexec

SELECT format('GRANT ALL PRIVILEGES ON DATABASE %I TO %I', :'marquez_db', :'marquez_user')
\gexec

-- Grant schema privileges
\connect :analytics_db
GRANT ALL ON SCHEMA public TO :analytics_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO :analytics_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO :analytics_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON FUNCTIONS TO :analytics_user;

\connect :dagster_db
GRANT ALL ON SCHEMA public TO :dagster_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO :dagster_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO :dagster_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON FUNCTIONS TO :dagster_user;

\connect :marquez_db
GRANT ALL ON SCHEMA public TO :marquez_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO :marquez_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO :marquez_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON FUNCTIONS TO :marquez_user;
SQL
