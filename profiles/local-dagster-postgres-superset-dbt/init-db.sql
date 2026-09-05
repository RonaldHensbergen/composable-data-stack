-- Initialize databases for the local-dagster-postgres-superset profile.
-- Safe to run multiple times.

DO $$
BEGIN
  CREATE USER dagster WITH PASSWORD 'dagster_password';
EXCEPTION WHEN duplicate_object THEN
  ALTER USER dagster WITH PASSWORD 'dagster_password';
END $$;

DO $$
BEGIN
  CREATE USER superset WITH PASSWORD 'superset_password';
EXCEPTION WHEN duplicate_object THEN
  ALTER USER superset WITH PASSWORD 'superset_password';
END $$;

DO $$
BEGIN
  CREATE USER analytics WITH PASSWORD 'analytics_password';
EXCEPTION WHEN duplicate_object THEN
  ALTER USER analytics WITH PASSWORD 'analytics_password';
END $$;


SELECT 'CREATE DATABASE analytics'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'analytics')
\gexec

SELECT 'CREATE DATABASE dagster'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'dagster')
\gexec

SELECT 'CREATE DATABASE superset'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'superset')
\gexec

-- Grant database-level privileges to the analytics user.
GRANT ALL PRIVILEGES ON DATABASE analytics TO analytics;
GRANT ALL PRIVILEGES ON DATABASE dagster TO analytics;
GRANT ALL PRIVILEGES ON DATABASE superset TO analytics;

-- Per-database schema/default privileges.
\connect analytics
GRANT ALL ON SCHEMA public TO analytics;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO analytics;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO analytics;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON FUNCTIONS TO analytics;

\connect dagster
GRANT ALL PRIVILEGES ON SCHEMA public TO dagster;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO dagster;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO dagster;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON FUNCTIONS TO dagster;

\connect superset
GRANT ALL ON SCHEMA public TO superset;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO superset;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO superset;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON FUNCTIONS TO superset;

-- Verify databases exist.
\l
