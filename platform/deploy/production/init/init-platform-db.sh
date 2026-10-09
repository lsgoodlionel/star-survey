#!/bin/sh
set -eu

export PLATFORM_DB_OWNER_PASSWORD="$(cat "$PLATFORM_DB_OWNER_PASSWORD_FILE")"
export PLATFORM_DB_APP_PASSWORD="$(cat "$PLATFORM_DB_APP_PASSWORD_FILE")"

psql --set=ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'SQL'
\getenv owner_password PLATFORM_DB_OWNER_PASSWORD
\getenv app_password PLATFORM_DB_APP_PASSWORD
SELECT format('CREATE ROLE platform_owner LOGIN PASSWORD %L NOSUPERUSER CREATEDB NOBYPASSRLS', :'owner_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'platform_owner')
\gexec
SELECT format('CREATE ROLE platform_app LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOBYPASSRLS', :'app_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'platform_app')
\gexec
SELECT 'CREATE DATABASE platform OWNER platform_owner'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'platform')
\gexec
GRANT CONNECT ON DATABASE platform TO platform_app;
SQL

unset PLATFORM_DB_OWNER_PASSWORD PLATFORM_DB_APP_PASSWORD
