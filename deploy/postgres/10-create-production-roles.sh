#!/usr/bin/env bash
# Runs only when PostgreSQL initializes an empty data volume.  The API retains
# migration ownership through the bootstrap account, while Keycloak receives a
# separate schema-owned account.
set -Eeuo pipefail

: "${KEYCLOAK_DATABASE_PASSWORD:?KEYCLOAK_DATABASE_PASSWORD is required}"

psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --set=ON_ERROR_STOP=1 \
  --set=keycloak_password="$KEYCLOAK_DATABASE_PASSWORD" <<'SQL'
SELECT format('CREATE ROLE keycloak LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD %L', :'keycloak_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'keycloak')\gexec
ALTER ROLE keycloak NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD :'keycloak_password';
CREATE SCHEMA IF NOT EXISTS keycloak AUTHORIZATION keycloak;
GRANT USAGE, CREATE ON SCHEMA keycloak TO keycloak;
SQL
