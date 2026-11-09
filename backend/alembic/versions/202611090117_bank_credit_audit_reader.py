"""Attribute new CREDIT audit subjects to one bank, preserving shared history.

The historical migration is a frozen SQL dependency, never today's application
ownership registry. The new bank-local CREDIT entity types are NOT
shared-register types. Existing function ownership, execution ACLs and security
configuration survive CREATE OR REPLACE; no identity, binding or session row
changes.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import cast

from alembic.util.pyfiles import load_python_file

from alembic import op

revision = "202611090117"
down_revision = "202611090116"
branch_labels = None
depends_on = None

LOCAL_SUBJECTS = (
    "'bank_liquidity_threshold', 'bank_liquidity_haircut', "
    "'bank_concentration_limit', 'bank_credit_threshold', "
    "'bank_ecl_assumption', 'bank_crm_haircut'"
)
SHARED_SUBJECTS = (
    "param_concentration_limit",
    "param_credit_threshold",
    "param_crm_haircut",
    "param_ecl_assumption",
    "param_liquidity_haircut",
    "param_liquidity_threshold",
)
SESSION_BANK = "current_setting('app.audit_bank_id', true)"
SESSION_ORGANIZATION = "current_setting('app.organization_id', true)"


def _historical_sql() -> tuple[str, str]:
    # Alembic's loader returns a dynamic module; isolate that typed boundary.
    namespace = cast(
        Mapping[str, object],
        vars(load_python_file(str(Path(__file__).parent), "202610120100_bank_audit_reader.py")),
    )
    tenant = namespace["_ATTRIBUTABLE_TENANT_EVENT"]
    staff = namespace["_ATTRIBUTABLE_STAFF_EVENT"]
    if not isinstance(tenant, str) or not isinstance(staff, str):
        raise RuntimeError("Expected frozen audit attribution SQL")
    return tenant, staff


def _quoted_schema() -> str:
    # Raw SQLAlchemy scalars have no static type; validate this SQL boundary.
    schema = cast(object, op.get_bind().exec_driver_sql("SELECT current_schema()").scalar_one())
    if not isinstance(schema, str):
        raise RuntimeError("Expected a migration schema")
    return op.get_bind().dialect.identifier_preparer.quote_identifier(schema)


def _replace_readers(*, bank_local: bool) -> None:
    tenant, staff = _historical_sql()
    shared = ", ".join(f"'{kind}'" for kind in SHARED_SUBJECTS)
    if bank_local:
        local = LOCAL_SUBJECTS
        # Require the retained platform-ID owner even if details names a bank.
        # An unknown/cross-tenant owner cannot borrow a matching detail reference.
        direct = f"""
            e.organization_id = {SESSION_ORGANIZATION}
            AND e.entity_type IN ({local})
            AND e.entity_id = {SESSION_BANK}
            AND EXISTS (SELECT 1 FROM banks b WHERE b.id = e.entity_id
                        AND b.organization_id = e.organization_id)
            AND (e.details->>'bank_id' IS NULL OR e.details->>'bank_id' = {SESSION_BANK})
            AND (e.details->'scope'->>'institution_id' IS NULL
                 OR e.details->'scope'->>'institution_id' = {SESSION_BANK})
        """
        tenant = (
            f"(({direct}) OR ((e.entity_type IS NULL OR "
            f"e.entity_type NOT IN ({local})) AND ({tenant})))"
        )
    quoted = _quoted_schema()
    session_uuid = (
        "CASE WHEN e.session_id ~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-"
        "[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'"
        " THEN CAST(e.session_id AS uuid) END"
    )
    op.execute(f"""
        ALTER POLICY bank_audit_entries_read ON audit_chain_entries USING (
            (stream = 'audit_events' AND EXISTS (
                SELECT 1 FROM audit_events e
                WHERE e.id = audit_chain_entries.event_id AND {tenant}
            ))
            OR (stream = 'operator_audit_log' AND EXISTS (
                SELECT 1 FROM operator_audit_log e
                WHERE e.id = audit_chain_entries.event_id AND {staff}
            ))
        );
    """)
    # Keep the frozen bodies byte-for-byte on downgrade, not just equivalent SQL.
    op.execute(f"""
        CREATE OR REPLACE FUNCTION bank_audit_previous_link(
            wanted_stream text, wanted_sequence bigint, wanted_hash text
        )
        RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, {quoted}, pg_temp AS $fn$
            SELECT EXISTS(
                SELECT 1
                FROM audit_chain_entries c
                WHERE
                    c.stream = wanted_stream
                    AND c.sequence = wanted_sequence
                    AND c.previous_hash = wanted_hash
                    AND (
                        (c.stream = 'audit_events' AND EXISTS (
                            SELECT 1
                            FROM audit_events e
                            WHERE e.id = c.event_id AND {tenant}
                        ))
                        OR (c.stream = 'operator_audit_log' AND EXISTS (
                            SELECT 1
                            FROM operator_audit_log e
                            WHERE e.id = c.event_id AND {staff}
                        ))
                    )
                    AND c.previous_hash = CASE
                        WHEN c.sequence = 1 THEN repeat('0', 64)
                        ELSE (
                            SELECT p.entry_hash
                            FROM audit_chain_entries p
                            WHERE p.stream = c.stream AND p.sequence = c.sequence - 1
                        )
                    END
            )
        $fn$;
    """)
    op.execute(f"""
        CREATE OR REPLACE FUNCTION bank_audit_inspection_context(
            wanted_stream text, wanted_event uuid
        )
        RETURNS jsonb LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, {quoted}, pg_temp AS $fn$
            WITH evidence AS (
                SELECT
                    e.organization_id,
                    e.details ->> 'act_operator' AS operator_email,
                    e.details ->> 'impersonation_session_id' AS session_id
                FROM audit_events e
                WHERE
                    wanted_stream = 'audit_events'
                    AND e.id = wanted_event AND {tenant}
                    AND (
                        e.entity_type IS NULL OR e.entity_type NOT IN (
                            {shared}
                        ) OR e.entity_id = {SESSION_BANK}
                    )
                UNION ALL
                SELECT
                    e.target_org,
                    e.operator_email,
                    e.detail ->> 'session_id'
                FROM operator_audit_log e
                WHERE
                    wanted_stream = 'operator_audit_log'
                    AND e.id = wanted_event AND {staff}
            )

            SELECT
                jsonb_build_object(
                    'reason', s.reason, 'support_reference', s.support_reference
                )
            FROM evidence e
            JOIN operator_inspector_sessions s
                ON
                    s.id = {session_uuid}
                    AND s.organization_id = e.organization_id
                    AND s.started_by = e.operator_email
        $fn$;
    """)


def upgrade() -> None:
    _replace_readers(bank_local=True)


def downgrade() -> None:
    _replace_readers(bank_local=False)
