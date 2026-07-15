#!/usr/bin/env bash
set -euo pipefail

POSTGRES_HOST="${POSTGRES_HOST:-localhost}"
POSTGRES_PORT="${POSTGRES_PORT:-15432}"
POSTGRES_ADMIN_DB="${POSTGRES_ADMIN_DB:-postgres}"
POSTGRES_ADMIN_USER="${POSTGRES_ADMIN_USER:-postgres}"
POSTGRES_ADMIN_PASSWORD="${POSTGRES_ADMIN_PASSWORD:-postgres}"

RISK_DB_NAME="${RISK_DB_NAME:-risk_service}"
RISK_MIGRATION_ROLE="${RISK_MIGRATION_ROLE:-risk_service_migrator}"
RISK_MIGRATION_PASSWORD="${RISK_MIGRATION_PASSWORD:-risk_service_migrator}"
RISK_APP_ROLE="${RISK_APP_ROLE:-risk_service_app}"
RISK_APP_PASSWORD="${RISK_APP_PASSWORD:-risk_service_app}"
RISK_DEMO_ORG_1_ID="${RISK_DEMO_ORG_1_ID:-11111111-1111-4111-8111-111111111111}"
RISK_DEMO_ORG_2_ID="${RISK_DEMO_ORG_2_ID:-22222222-2222-4222-8222-222222222222}"
RISK_DEMO_USER_1_ID="${RISK_DEMO_USER_1_ID:-aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa}"
RISK_DEMO_USER_2_ID="${RISK_DEMO_USER_2_ID:-bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb}"

export PGPASSWORD="${POSTGRES_ADMIN_PASSWORD}"

admin_psql() {
  psql \
    --host "${POSTGRES_HOST}" \
    --port "${POSTGRES_PORT}" \
    --username "${POSTGRES_ADMIN_USER}" \
    --dbname "${POSTGRES_ADMIN_DB}" \
    --set ON_ERROR_STOP=1 \
    "$@"
}

db_psql() {
  psql \
    --host "${POSTGRES_HOST}" \
    --port "${POSTGRES_PORT}" \
    --username "${POSTGRES_ADMIN_USER}" \
    --dbname "${RISK_DB_NAME}" \
    --set ON_ERROR_STOP=1 \
    "$@"
}

role_exists() {
  admin_psql --tuples-only --no-align --command \
    "SELECT 1 FROM pg_roles WHERE rolname = '$1'" | grep -q 1
}

database_exists() {
  admin_psql --tuples-only --no-align --command \
    "SELECT 1 FROM pg_database WHERE datname = '$1'" | grep -q 1
}

create_or_update_role() {
  local role_name="$1"
  local role_password="$2"
  local role_options="$3"

  if role_exists "${role_name}"; then
    admin_psql --command "ALTER ROLE ${role_name} WITH PASSWORD '${role_password}' ${role_options};"
  else
    admin_psql --command "CREATE ROLE ${role_name} LOGIN PASSWORD '${role_password}' ${role_options};"
  fi
}

create_or_update_role \
  "${RISK_MIGRATION_ROLE}" \
  "${RISK_MIGRATION_PASSWORD}" \
  "NOSUPERUSER NOCREATEDB NOCREATEROLE INHERIT BYPASSRLS"

create_or_update_role \
  "${RISK_APP_ROLE}" \
  "${RISK_APP_PASSWORD}" \
  "NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS"

if ! database_exists "${RISK_DB_NAME}"; then
  admin_psql --command "CREATE DATABASE ${RISK_DB_NAME} OWNER ${RISK_MIGRATION_ROLE};"
fi

admin_psql --command "ALTER DATABASE ${RISK_DB_NAME} OWNER TO ${RISK_MIGRATION_ROLE};"

db_psql <<SQL
ALTER SCHEMA public OWNER TO ${RISK_MIGRATION_ROLE};

DO \$\$
DECLARE
  obj record;
BEGIN
  FOR obj IN
    SELECT schemaname, tablename
    FROM pg_tables
    WHERE schemaname = 'public'
  LOOP
    EXECUTE format('ALTER TABLE %I.%I OWNER TO %I', obj.schemaname, obj.tablename, '${RISK_MIGRATION_ROLE}');
  END LOOP;

  FOR obj IN
    SELECT sequence_schema, sequence_name
    FROM information_schema.sequences
    WHERE sequence_schema = 'public'
  LOOP
    EXECUTE format('ALTER SEQUENCE %I.%I OWNER TO %I', obj.sequence_schema, obj.sequence_name, '${RISK_MIGRATION_ROLE}');
  END LOOP;

  FOR obj IN
    SELECT table_schema, table_name
    FROM information_schema.views
    WHERE table_schema = 'public'
  LOOP
    EXECUTE format('ALTER VIEW %I.%I OWNER TO %I', obj.table_schema, obj.table_name, '${RISK_MIGRATION_ROLE}');
  END LOOP;
END
\$\$;

GRANT CONNECT ON DATABASE ${RISK_DB_NAME} TO ${RISK_APP_ROLE};
GRANT USAGE ON SCHEMA public TO ${RISK_APP_ROLE};
GRANT CREATE, USAGE ON SCHEMA public TO ${RISK_MIGRATION_ROLE};
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO ${RISK_MIGRATION_ROLE};
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO ${RISK_MIGRATION_ROLE};

ALTER DEFAULT PRIVILEGES FOR ROLE ${RISK_MIGRATION_ROLE} IN SCHEMA public
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO ${RISK_APP_ROLE};

ALTER DEFAULT PRIVILEGES FOR ROLE ${RISK_MIGRATION_ROLE} IN SCHEMA public
GRANT USAGE, SELECT ON SEQUENCES TO ${RISK_APP_ROLE};
SQL

export DATABASE_URL="postgresql+psycopg://${RISK_MIGRATION_ROLE}:${RISK_MIGRATION_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${RISK_DB_NAME}"
uv run alembic upgrade head

db_psql <<SQL
INSERT INTO organizations (id, name, created_at, updated_at)
VALUES
  ('${RISK_DEMO_ORG_1_ID}', 'Demo Tenant 1', now(), now()),
  ('${RISK_DEMO_ORG_2_ID}', 'Demo Tenant 2', now(), now())
ON CONFLICT (id) DO UPDATE
SET name = EXCLUDED.name,
    updated_at = now();

INSERT INTO users (id, organization_id, email, display_name, is_active, created_at, updated_at)
VALUES
  (
    '${RISK_DEMO_USER_1_ID}',
    '${RISK_DEMO_ORG_1_ID}',
    'demo.user.one@example.test',
    'Demo User One',
    true,
    now(),
    now()
  ),
  (
    '${RISK_DEMO_USER_2_ID}',
    '${RISK_DEMO_ORG_2_ID}',
    'demo.user.two@example.test',
    'Demo User Two',
    true,
    now(),
    now()
  )
ON CONFLICT (id) DO UPDATE
SET organization_id = EXCLUDED.organization_id,
    email = EXCLUDED.email,
    display_name = EXCLUDED.display_name,
    is_active = EXCLUDED.is_active,
    updated_at = now();

GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO ${RISK_APP_ROLE};
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO ${RISK_APP_ROLE};
SQL

cat <<EOF
Database bootstrap complete.

Migration URL:
  postgresql+psycopg://${RISK_MIGRATION_ROLE}:${RISK_MIGRATION_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${RISK_DB_NAME}

App DATABASE_URL:
  postgresql+psycopg://${RISK_APP_ROLE}:${RISK_APP_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${RISK_DB_NAME}

Demo tenant headers:
  X-Org-Id: ${RISK_DEMO_ORG_1_ID}
  X-User-Id: ${RISK_DEMO_USER_1_ID}
EOF
