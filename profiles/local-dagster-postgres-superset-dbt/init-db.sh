#!/bin/bash
set -e

psql -v ON_ERROR_STOP=1 \
     -U "$POSTGRES_USER" \
     -d "$POSTGRES_DB" \
           -v dagster_password="$DAGSTER_DB_PASSWORD" \
           -v superset_password="$SUPERSET_DB_PASSWORD" \
           -v analytics_password="$ANALYTICS_DB_PASSWORD" \
           -v analytics_db="$ANALYTICS_DB_NAME" \
           -v analytics_user="$ANALYTICS_DB_USER" \
           -v dagster_db="$DAGSTER_DB_NAME" \
           -v dagster_user="$DAGSTER_DB_USER" \
           -v superset_db="$SUPERSET_DB_NAME" \
           -v superset_user="$SUPERSET_DB_USER" <<'SQL'
-- Create users if they don't exist
SELECT format('CREATE USER %I WITH PASSWORD %L', :'analytics_user', :'analytics_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'analytics_user')
\gexec

SELECT format('CREATE USER %I WITH PASSWORD %L', :'dagster_user', :'dagster_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'dagster_user')
\gexec

SELECT format('CREATE USER %I WITH PASSWORD %L', :'superset_user', :'superset_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'superset_user')
\gexec

-- Create databases if they don't exist
SELECT format('CREATE DATABASE %I', :'analytics_db')
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = :'analytics_db')
\gexec

SELECT format('CREATE DATABASE %I', :'dagster_db')
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = :'dagster_db')
\gexec

SELECT format('CREATE DATABASE %I', :'superset_db')
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = :'superset_db')
\gexec

-- Grant privileges on databases
SELECT format('GRANT ALL PRIVILEGES ON DATABASE %I TO %I', :'analytics_db', :'analytics_user')
\gexec

SELECT format('GRANT ALL PRIVILEGES ON DATABASE %I TO %I', :'dagster_db', :'dagster_user')
\gexec

SELECT format('GRANT ALL PRIVILEGES ON DATABASE %I TO %I', :'superset_db', :'superset_user')
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

\connect :superset_db
GRANT ALL ON SCHEMA public TO :superset_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO :superset_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO :superset_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON FUNCTIONS TO :superset_user;
SQL
