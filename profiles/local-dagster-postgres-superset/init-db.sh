#!/bin/bash
set -e

psql -v ON_ERROR_STOP=1 \
     -U "$POSTGRES_USER" \
     -d "$POSTGRES_DB" \
           -v dagster_password="$DAGSTER_DB_PASSWORD" \
           -v superset_password="$SUPERSET_DB_PASSWORD" \
           -v analytics_password="$ANALYTICS_DB_PASSWORD" \
           -v analytics_db="$ANALYTICS_DB_NAME" \
           -v analytics_user="$ANALYTICS_DB_USER" <<'SQL'
SELECT format('CREATE USER %I WITH PASSWORD %L', :'analytics_user', :'analytics_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'analytics_user')
\gexec
SELECT format('ALTER USER %I WITH PASSWORD %L', :'analytics_user', :'analytics_password')
\gexec

SELECT format('CREATE USER superset WITH PASSWORD %L', :'superset_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'superset')
\gexec
SELECT format('ALTER USER superset WITH PASSWORD %L', :'superset_password')
\gexec

SELECT format('CREATE USER analytics WITH PASSWORD %L', :'analytics_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'analytics')
\gexec
SELECT format('ALTER USER analytics WITH PASSWORD %L', :'analytics_password')
\gexec

SELECT format('CREATE DATABASE %I', :'analytics_db')
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = :'analytics_db')
\gexec

SELECT 'CREATE DATABASE dagster'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'dagster')
\gexec

SELECT 'CREATE DATABASE superset'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'superset')
\gexec

GRANT ALL PRIVILEGES ON DATABASE dagster TO dagster;
GRANT ALL PRIVILEGES ON DATABASE superset TO superset;

DO $do$
BEGIN
  EXECUTE format('GRANT ALL PRIVILEGES ON DATABASE %I TO %I', :'analytics_db', :'analytics_user');
END
$do$;

\connect :analytics_db
GRANT ALL ON SCHEMA public TO :analytics_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO :analytics_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO :analytics_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON FUNCTIONS TO :analytics_user;

\connect dagster
GRANT ALL ON SCHEMA public TO dagster;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO dagster;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO dagster;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON FUNCTIONS TO dagster;

\connect superset
GRANT ALL ON SCHEMA public TO superset;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO superset;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO superset;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON FUNCTIONS TO superset;
SQL
