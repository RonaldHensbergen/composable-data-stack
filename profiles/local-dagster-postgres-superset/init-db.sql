-- Initialize databases for the local-dagster-postgres-superset profile.
-- Safe to run multiple times.

-- PostgreSQL does not support CREATE DATABASE IF NOT EXISTS, so use psql \gexec.
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
GRANT ALL ON SCHEMA public TO analytics;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO analytics;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO analytics;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON FUNCTIONS TO analytics;

\connect superset
GRANT ALL ON SCHEMA public TO analytics;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO analytics;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO analytics;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON FUNCTIONS TO analytics;

-- Verify databases exist.
\l
