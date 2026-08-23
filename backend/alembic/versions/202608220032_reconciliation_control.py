"""Fail-closed reconciliation control (enterprise audit 2026-08-20, P0-10).

Two additions, both governance surface — no calculation input, hash or output
is rewritten by this migration:

1. ``reconciliation_exceptions`` — the governed escape valve for a blocked
   filing (reason, requester, approver, approval timestamp, effective window,
   breach ceiling, revocation). Tenant-scoped and FORCE-RLS like every other
   table carrying a bank's data.
2. The ``balance_identity_tolerance_pct`` rows in the regulatory-parameter
   control plane, so the balance-sheet identity tolerance is a governed,
   effective-dated, operator-editable number rather than the hardcoded
   ``BALANCE_GAP_WARN_FRACTION = 0.005`` it replaces. Inserted idempotently
   from ``regulatory_parameters.SEED_PARAMETERS`` (the single catalogue), so a
   database already carrying the row is left alone.

Revision ID: 202608220032
Revises: 202608220031
"""

from __future__ import annotations

from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op
from app.core.ids import new_uuid4

revision = "202608220032"
down_revision = "202608220031"
branch_labels = None
depends_on = None

_TABLE = "reconciliation_exceptions"
_PARAM_TABLE = "regulatory_parameter"
_PARAM_CODE = "balance_identity_tolerance_pct"

# Post-platform-ID-epoch policy form: text comparison, never a ::uuid cast.
# An unset GUC yields NULL, so the predicate fails closed (no rows).
_TENANT_ID_EXPR = "NULLIF(current_setting('app.organization_id', true), '')"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("organization_id", sa.String(16), nullable=False),
        sa.Column("bank_id", sa.String(16), nullable=False),
        sa.Column("control", sa.String(48), nullable=False),
        sa.Column("max_gap_fraction", sa.Numeric(9, 6), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("requested_by", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_by", sa.String(120), nullable=False),
        sa.Column("approved_by_user_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("approval_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.String(120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "control IN ('balance_sheet_identity')",
            name="ck_reconciliation_exceptions_control",
        ),
        sa.CheckConstraint(
            "max_gap_fraction > 0",
            name="ck_reconciliation_exceptions_max_gap_positive",
        ),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_reconciliation_exceptions_window",
        ),
        sa.CheckConstraint(
            "length(trim(reason)) > 0",
            name="ck_reconciliation_exceptions_reason_present",
        ),
        sa.ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["banks.id", "banks.organization_id"],
        ),
    )
    op.create_index(
        "ix_reconciliation_exceptions_lookup",
        _TABLE,
        ["organization_id", "bank_id", "control", "effective_from"],
    )

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY {_TABLE}_tenant_isolation ON {_TABLE}
            FOR ALL
            USING ((organization_id)::text = {_TENANT_ID_EXPR})
            WITH CHECK ((organization_id)::text = {_TENANT_ID_EXPR})
            """
        )

    _seed_tolerance_parameter()


def _seed_tolerance_parameter() -> None:
    """Insert the governed tolerance rows, skipping any that already exist."""
    # Deferred import: the single seed catalogue lives with the resolver.
    from app.services.regulatory_parameters import (  # noqa: PLC0415
        SEED_EFFECTIVE_FROM,
        seed_rows,
    )

    bind = op.get_bind()
    existing = {
        (scope_type, scope_key, jurisdiction)
        for scope_type, scope_key, jurisdiction in bind.execute(
            sa.text(
                f"SELECT scope_type, scope_key, jurisdiction_code FROM {_PARAM_TABLE} "  # noqa: S608
                "WHERE param_code = :code"
            ),
            {"code": _PARAM_CODE},
        )
    }
    pending = [
        row
        for row in seed_rows()
        if row["param_code"] == _PARAM_CODE
        and (row["scope_type"], row["scope_key"], row["jurisdiction_code"]) not in existing
    ]
    if not pending:
        return
    now = datetime.now(UTC)
    seed_table = sa.table(
        _PARAM_TABLE,
        sa.column("id", sa.Uuid(as_uuid=True)),
        sa.column("scope_type", sa.String),
        sa.column("scope_key", sa.String),
        sa.column("param_code", sa.String),
        sa.column("jurisdiction_code", sa.String),
        sa.column("value_numeric", sa.Numeric),
        sa.column("value_json", sa.JSON),
        sa.column("unit", sa.String),
        sa.column("source_citation", sa.String),
        sa.column("confirmation_status", sa.String),
        sa.column("effective_from", sa.Date),
        sa.column("effective_to", sa.Date),
        sa.column("status", sa.String),
        sa.column("proposed_by", sa.String),
        sa.column("approved_by", sa.String),
        sa.column("approved_at", sa.DateTime(timezone=True)),
        sa.column("change_rationale", sa.String),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    op.bulk_insert(
        seed_table,
        [
            {
                **row,
                "id": new_uuid4(),
                "effective_from": SEED_EFFECTIVE_FROM,
                "approved_at": now,
                "change_rationale": None,
                "created_at": now,
                "updated_at": now,
            }
            for row in pending
        ],
    )


def downgrade() -> None:
    op.execute(f"DELETE FROM {_PARAM_TABLE} WHERE param_code = '{_PARAM_CODE}'")  # noqa: S608
    if op.get_bind().dialect.name == "postgresql":
        op.execute(f"DROP POLICY IF EXISTS {_TABLE}_tenant_isolation ON {_TABLE}")
        op.execute(f"ALTER TABLE {_TABLE} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {_TABLE} DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_reconciliation_exceptions_lookup", table_name=_TABLE)
    op.drop_table(_TABLE)
