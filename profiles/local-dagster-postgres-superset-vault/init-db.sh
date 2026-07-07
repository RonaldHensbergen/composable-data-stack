#!/bin/bash
set -e

psql -v ON_ERROR_STOP=1 \
  -U "$POSTGRES_USER" \
  -d "$POSTGRES_DB" \
     -v dagster_password="$DAGSTER_POSTGRES_PASSWORD" \
     -v superset_password="$SUPERSET_POSTGRES_PASSWORD" \
     -v analytics_password="$ANALYTICS_POSTGRES_PASSWORD" <<'SQL'
SELECT format('CREATE USER dagster WITH PASSWORD %L', :'dagster_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'dagster')
\gexec
SELECT format('ALTER USER dagster WITH PASSWORD %L', :'dagster_password')
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

SELECT 'CREATE DATABASE analytics'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'analytics')
\gexec

SELECT 'CREATE DATABASE dagster'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'dagster')
\gexec

SELECT 'CREATE DATABASE superset'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'superset')
\gexec

GRANT ALL PRIVILEGES ON DATABASE analytics TO analytics;
GRANT ALL PRIVILEGES ON DATABASE dagster TO dagster;
GRANT ALL PRIVILEGES ON DATABASE superset TO superset;

\connect analytics
GRANT ALL ON SCHEMA public TO analytics;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO analytics;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO analytics;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON FUNCTIONS TO analytics;

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
