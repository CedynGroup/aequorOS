"""Let a login that does not bypass RLS resolve a sign-in's tenant, and nothing more.

Revision ID: 202611210119
Revises: 202611140118

Sign-in starts before any tenant is known, while ``users``, ``sso_connections`` and
``audit_events`` force row-level security. #564 moved those lookups from the
BYPASSRLS worker credential to the API's ``DATABASE_URL``. A login that does not
bypass RLS (production's API login today, ``aqs_api`` after its cutover) then sees
no accounts at all, so nobody can sign in, and recording the failed attempt with no
organization is itself refused.

Four ``SECURITY DEFINER`` functions owned by NOLOGIN ``aqs_owner`` answer only what
sign-in must know before it has a tenant: which tenant an active account's email,
or an enabled SSO issuer and audience, belongs to; and recording a failed attempt
that matched no account. The application then enters that tenant and does
everything else under ordinary RLS. ``aqs_owner`` reads only the routing columns
through SELECT-only policies, and may insert only unattributed sign-in failures
into the audit log. EXECUTE goes to ``aqs_api`` and to the login that owns
``users`` (the legacy API credential). No role gains BYPASSRLS or table access.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from typing import cast

from alembic import op

revision = "202611210119"
down_revision = "202611140118"
branch_labels = None
depends_on = None

OWNER = "aqs_owner"
AUDIT_OWNER = "aequoros_audit_owner"
POLICY = "aqs_owner_sign_in_lookup"
AUDIT_POLICY = "aqs_owner_unattributed_sign_in_failure"
FUNCTIONS = (
    "aqs_auth_login_organizations(text)",
    "aqs_auth_sso_organizations(text,text[])",
    "aqs_auth_enabled_sso_organizations()",
    "aqs_auth_record_unattributed_failure(text,uuid,jsonb,timestamptz)",
)
FAILURES = ("authentication.password.failed", "authentication.sso.failed")
ROUTING_COLUMNS = {
    "users": "organization_id, email, is_active",
    "sso_connections": "organization_id, issuer, client_id, enabled",
}


@contextmanager
def _as_owner(owner: str) -> Generator[None]:
    connection = op.get_bind()
    previous = cast(str, connection.exec_driver_sql("SELECT current_user").scalar_one())
    quote = connection.dialect.identifier_preparer.quote_identifier
    op.execute(f"SET LOCAL ROLE {quote(owner)}")
    yield
    op.execute(f"SET LOCAL ROLE {quote(previous)}")


def _legacy_api_login(schema: str) -> str | None:
    """The login that owns ``users`` is the API credential until the aqs_owner handoff."""
    owner = cast(
        str | None,
        op.get_bind()
        .exec_driver_sql(
            "SELECT r.rolname FROM pg_class c JOIN pg_roles r ON r.oid = c.relowner "
            f"WHERE c.oid = '{schema}.users'::regclass "
            "AND r.rolcanlogin AND NOT r.rolsuper AND r.rolname <> 'aqs_owner'"
        )
        .scalar_one_or_none(),
    )
    return owner


def upgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name != "postgresql":
        return
    quote = connection.dialect.identifier_preparer.quote_identifier
    schema = quote(cast(str, connection.exec_driver_sql("SELECT current_schema()").scalar_one()))
    for table, columns in ROUTING_COLUMNS.items():
        op.execute(f"GRANT SELECT ({columns}) ON {schema}.{table} TO {OWNER}")
        op.execute(f"CREATE POLICY {POLICY} ON {schema}.{table} FOR SELECT TO {OWNER} USING (true)")
    failures = ", ".join(f"'{event}'" for event in FAILURES)
    with _as_owner(AUDIT_OWNER):
        op.execute(f"GRANT INSERT ON {schema}.audit_events TO {OWNER}")
        op.execute(f"""
            CREATE POLICY {AUDIT_POLICY} ON {schema}.audit_events FOR INSERT TO {OWNER}
            WITH CHECK (organization_id IS NULL AND actor_user_id IS NULL
                        AND entity_type = 'authentication_attempt'
                        AND event_type IN ({failures}))
        """)
    op.execute(f"""
        CREATE FUNCTION {schema}.aqs_auth_login_organizations(p_email text)
        RETURNS SETOF text LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, {schema}, pg_temp SET row_security = on AS $fn$
            SELECT u.organization_id::text FROM {schema}.users u
            WHERE u.email = p_email AND u.is_active
        $fn$
    """)
    op.execute(f"""
        CREATE FUNCTION {schema}.aqs_auth_sso_organizations(p_issuer text, p_audiences text[])
        RETURNS SETOF text LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, {schema}, pg_temp SET row_security = on AS $fn$
            SELECT c.organization_id::text FROM {schema}.sso_connections c
            WHERE c.enabled AND c.issuer = p_issuer AND c.client_id = ANY(p_audiences)
        $fn$
    """)
    op.execute(f"""
        CREATE FUNCTION {schema}.aqs_auth_enabled_sso_organizations()
        RETURNS SETOF text LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, {schema}, pg_temp SET row_security = on AS $fn$
            SELECT c.organization_id::text FROM {schema}.sso_connections c WHERE c.enabled
        $fn$
    """)
    op.execute(f"""
        CREATE FUNCTION {schema}.aqs_auth_record_unattributed_failure(
            p_event_type text, p_attempt_id uuid, p_details jsonb, p_occurred_at timestamptz
        ) RETURNS void LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, {schema}, pg_temp SET row_security = on AS $fn$
        BEGIN
            IF p_event_type IS NULL OR p_event_type NOT IN ({failures}) THEN
                RAISE EXCEPTION 'Only unattributed sign-in failures are recorded here';
            END IF;
            INSERT INTO {schema}.audit_events
                (id, organization_id, actor_user_id, event_type, entity_type, entity_id,
                 details, created_at)
            VALUES (gen_random_uuid(), NULL, NULL, p_event_type, 'authentication_attempt',
                    p_attempt_id::text, p_details, p_occurred_at);
        END
        $fn$
    """)
    callers = ["aqs_api"]
    legacy = _legacy_api_login(schema)
    if legacy is not None:
        callers.append(legacy)
    for signature in FUNCTIONS:
        function = f"{schema}.{signature}"
        op.execute(f"ALTER FUNCTION {function} OWNER TO {OWNER}")
        op.execute(f"REVOKE ALL ON FUNCTION {function} FROM PUBLIC")
        for role in callers:
            op.execute(f"GRANT EXECUTE ON FUNCTION {function} TO {quote(role)}")


def downgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name != "postgresql":
        return
    quote = connection.dialect.identifier_preparer.quote_identifier
    schema = quote(cast(str, connection.exec_driver_sql("SELECT current_schema()").scalar_one()))
    for signature in FUNCTIONS:
        op.execute(f"DROP FUNCTION {schema}.{signature}")
    with _as_owner(AUDIT_OWNER):
        op.execute(f"DROP POLICY {AUDIT_POLICY} ON {schema}.audit_events")
        op.execute(f"REVOKE INSERT ON {schema}.audit_events FROM {OWNER}")
    for table, columns in ROUTING_COLUMNS.items():
        op.execute(f"DROP POLICY {POLICY} ON {schema}.{table}")
        op.execute(f"REVOKE SELECT ({columns}) ON {schema}.{table} FROM {OWNER}")
