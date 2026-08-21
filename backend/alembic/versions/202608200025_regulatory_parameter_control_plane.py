"""regulatory_parameter control plane (SDI Phase C).

Creates the GLOBAL, non-RLS ``regulatory_parameter`` table — the operator-managed,
effective-dated, confirmation-status-aware store of class/type-keyed regulatory
numbers (docs/sdi.md §7 Phase C) — and seeds the authoritative §2.2/§4 catalogue.

The seed rows come from ``app.services.regulatory_parameters.seed_rows()`` — the
SINGLE catalogue the resolver and the hermetic-test seed also use, so the three
never drift. Regulatory changes AFTER this migration happen through the operator
console (new effective-dated generations), never by editing this file.

No RLS: this is a global reference table like ``institution_types`` /
``operator_audit_log``; mutations are operator-audited in ``operator_audit_log``.

Revision ID: 202608200025
Revises: 202608190024
"""

from __future__ import annotations

from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op
from app.core.ids import new_uuid4

revision = "202608200025"
down_revision = "202608190024"
branch_labels = None
depends_on = None

TABLE = "regulatory_parameter"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("scope_type", sa.String(length=24), nullable=False),
        sa.Column("scope_key", sa.String(length=40), nullable=False),
        sa.Column("param_code", sa.String(length=64), nullable=False),
        sa.Column("jurisdiction_code", sa.String(length=8), nullable=False),
        sa.Column("value_numeric", sa.Numeric(18, 6), nullable=True),
        sa.Column("value_json", sa.JSON(), nullable=True),
        sa.Column("unit", sa.String(length=24), nullable=False),
        sa.Column("source_citation", sa.String(length=240), nullable=False),
        sa.Column("confirmation_status", sa.String(length=12), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("proposed_by", sa.String(length=120), nullable=False),
        sa.Column("approved_by", sa.String(length=120), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("change_rationale", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "scope_type IN ('institution_class', 'institution_type')",
            name="ck_regulatory_parameter_scope_type",
        ),
        sa.CheckConstraint(
            "confirmation_status IN ('confirmed', 'pending')",
            name="ck_regulatory_parameter_confirmation_status",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'approved')",
            name="ck_regulatory_parameter_status",
        ),
        sa.CheckConstraint(
            "value_numeric IS NOT NULL OR value_json IS NOT NULL",
            name="ck_regulatory_parameter_value_present",
        ),
        sa.UniqueConstraint(
            "scope_type",
            "scope_key",
            "param_code",
            "jurisdiction_code",
            "effective_from",
            name="uq_regulatory_parameter_generation",
        ),
    )
    op.create_index(
        "ix_regulatory_parameter_resolution",
        TABLE,
        ["param_code", "scope_type", "scope_key", "jurisdiction_code", "effective_from"],
    )

    # Deferred import: the single seed catalogue lives with the resolver.
    from app.services.regulatory_parameters import seed_rows  # noqa: PLC0415

    now = datetime.now(UTC)
    seed_table = sa.table(
        TABLE,
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
                "approved_at": now,
                "change_rationale": None,
                "created_at": now,
                "updated_at": now,
            }
            for row in seed_rows()
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_regulatory_parameter_resolution", table_name=TABLE)
    op.drop_table(TABLE)
