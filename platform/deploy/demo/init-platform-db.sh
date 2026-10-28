#!/bin/sh
set -eu

: "${PLATFORM_DB_APP_PASSWORD:?missing PLATFORM_DB_APP_PASSWORD}"
: "${PLATFORM_DB_OWNER_PASSWORD:?missing PLATFORM_DB_OWNER_PASSWORD}"

psql --set=ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'SQL'
\getenv app_password PLATFORM_DB_APP_PASSWORD
\getenv owner_password PLATFORM_DB_OWNER_PASSWORD
SELECT format('CREATE ROLE platform_owner LOGIN PASSWORD %L NOSUPERUSER CREATEDB NOBYPASSRLS', :'owner_password')
\gexec
SELECT format('CREATE ROLE platform_app LOGIN PASSWORD %L NOSUPERUSER NOBYPASSRLS', :'app_password')
\gexec
CREATE DATABASE platform OWNER platform_owner;
GRANT CONNECT ON DATABASE platform TO platform_app;
SQL
