#!/bin/bash
set -e

psql -v ON_ERROR_STOP=1 \
     -U "$POSTGRES_USER" \
     -d "$POSTGRES_DB" \
           -v identity_password="$IDENTITY_DB_PASSWORD" \
           -v identity_db="$IDENTITY_DB_NAME" \
           -v identity_user="$IDENTITY_DB_USER" <<'SQL'
SELECT format('CREATE USER %I WITH PASSWORD %L', :'identity_user', :'identity_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'identity_user')
\gexec
SELECT format('ALTER USER %I WITH PASSWORD %L', :'identity_user', :'identity_password')
\gexec

SELECT format('CREATE DATABASE %I', :'identity_db')
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = :'identity_db')
\gexec

SELECT format('GRANT ALL PRIVILEGES ON DATABASE %I TO %I', :'identity_db', :'identity_user')
\gexec

\connect :identity_db
GRANT ALL ON SCHEMA public TO :identity_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO :identity_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO :identity_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON FUNCTIONS TO :identity_user;
SQL
