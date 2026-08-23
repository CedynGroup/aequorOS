"""Canonical withdrawal + the system-of-record register.

Three additions. No calculation input, hash or output changes: the new
``withdrawn_at`` column is NULL on every existing row, so
``superseded_by IS NULL AND withdrawn_at IS NULL`` selects exactly the rows
``superseded_by IS NULL`` selected before. Nothing is withdrawn by this
migration and nothing withdraws automatically, ever.

1. ``withdrawn_at`` / ``withdrawn_by_batch_id`` / ``withdrawal_reason`` on every
   canonical entity carrying ``CanonicalMetadataMixin``. Supersession can only
   retire a row by naming its REPLACEMENT, so it cannot express "this book is
   gone" — which is why ``CanonicalPosition.superseded_by`` was never assigned
   anywhere in the codebase (0 of 571,984 rows on the primary at build time) and
   why the platform's own remedy for duplicated source books ("withdraw the
   other system's data for this date") could not be performed.
   ``docs/data_engine.md`` §5.3 has required soft-delete markers in canonical
   since it was written.
2. The current-generation partial indexes are rebuilt over BOTH lifecycle
   columns, so a withdrawn book can be re-ingested without colliding with the
   evidence of its withdrawal.
3. ``system_of_record_declarations`` (which source system owns which position
   type, effective-dated and four-eyed) and ``canonical_withdrawals`` (the
   governed act itself). Both tenant-scoped and FORCE-RLS like every other table
   carrying a bank's data.

Revision ID: 202608230035
Revises: 202608220034
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202608230035"
down_revision = "202608220034"
branch_labels = None
depends_on = None

# Post-platform-ID-epoch policy form: text comparison, never a ::uuid cast.
# An unset GUC yields NULL, so the predicate fails closed (no rows).
_TENANT_ID_EXPR = "NULLIF(current_setting('app.organization_id', true), '')"

_DECLARATIONS = "system_of_record_declarations"
_WITHDRAWALS = "canonical_withdrawals"

#: Every table carrying ``CanonicalMetadataMixin``.
_CANONICAL_TABLES: tuple[str, ...] = (
    "canonical_counterparties",
    "canonical_counterparty_ratings",
    "canonical_fx_rates",
    "canonical_gl_accounts",
    "canonical_market_indices",
    "canonical_position_snapshots",
    "canonical_positions",
    "canonical_products",
    "canonical_yield_curve_points",
    "canonical_yield_curves",
)

_OLD_WHERE = "superseded_by IS NULL"
_NEW_WHERE = "superseded_by IS NULL AND withdrawn_at IS NULL"

#: (index name, table, index elements, unique, INCLUDE payload) for every partial
#: index over the current generation. Rebuilt so the predicate matches the
#: readers'.
#:
#: An element containing "(" is a SQL EXPRESSION, not a column, and is passed
#: through ``sa.text``. Two of these indexes key on ``coalesce(tenor_months, 0)``
#: / ``coalesce(horizon_months, 0)`` (migration 202607170006), and dropping the
#: expression on rebuild would narrow the natural key: every non-spot FX tenor
#: for one currency pair would collide. Verified against the model metadata with
#: ``alembic.autogenerate.compare_metadata`` after the chain ran.
_PARTIAL_INDEXES: tuple[tuple[str, str, tuple[str, ...], bool, tuple[str, ...]], ...] = (
    (
        "uq_canonical_counterparties_current",
        "canonical_counterparties",
        ("organization_id", "bank_id", "source_system", "source_reference", "as_of_date"),
        True,
        (),
    ),
    (
        "uq_canonical_counterparty_ratings_current",
        "canonical_counterparty_ratings",
        ("organization_id", "bank_id", "as_of_date", "issuer", "agency"),
        True,
        (),
    ),
    (
        "uq_canonical_fx_rates_current",
        "canonical_fx_rates",
        (
            "organization_id",
            "bank_id",
            "as_of_date",
            "base_currency",
            "quote_currency",
            "rate_type",
            "coalesce(tenor_months, 0)",
        ),
        True,
        (),
    ),
    (
        "uq_canonical_gl_accounts_current",
        "canonical_gl_accounts",
        ("organization_id", "bank_id", "account_code", "as_of_date"),
        True,
        (),
    ),
    (
        "uq_canonical_market_indices_current",
        "canonical_market_indices",
        (
            "organization_id",
            "bank_id",
            "as_of_date",
            "index_code",
            "scenario",
            "coalesce(horizon_months, 0)",
        ),
        True,
        (),
    ),
    (
        "uq_canonical_position_snapshots_current",
        "canonical_position_snapshots",
        ("organization_id", "position_id", "as_of_date"),
        True,
        (),
    ),
    (
        "uq_canonical_positions_current",
        "canonical_positions",
        ("organization_id", "bank_id", "source_system", "source_reference"),
        True,
        (),
    ),
    (
        "ix_canonical_positions_current_org_bank_type",
        "canonical_positions",
        ("organization_id", "bank_id", "position_type", "currency"),
        False,
        (),
    ),
    (
        "ix_canonical_positions_current_org_bank_ref",
        "canonical_positions",
        ("organization_id", "bank_id", "source_reference", "id"),
        False,
        ("position_type", "currency"),
    ),
    (
        "uq_canonical_products_current",
        "canonical_products",
        ("organization_id", "bank_id", "product_code", "as_of_date"),
        True,
        (),
    ),
    (
        "uq_canonical_yield_curve_points_current",
        "canonical_yield_curve_points",
        ("organization_id", "yield_curve_id", "tenor_months"),
        True,
        (),
    ),
    (
        "uq_canonical_yield_curves_current",
        "canonical_yield_curves",
        ("organization_id", "bank_id", "as_of_date", "currency", "curve_name"),
        True,
        (),
    ),
)


def _element(entry: str):  # noqa: ANN201 - str | sa.TextClause, per dialect
    """A column name, or a SQL expression when the entry carries one."""
    return sa.text(entry) if "(" in entry else entry


def _rebuild_partial_indexes(where: str) -> None:
    for name, table, columns, unique, include in _PARTIAL_INDEXES:
        op.drop_index(name, table_name=table)
        kwargs: dict = {"unique": unique, "postgresql_where": sa.text(where)}
        if op.get_bind().dialect.name == "postgresql":
            if include:
                kwargs["postgresql_include"] = list(include)
        else:
            kwargs["sqlite_where"] = sa.text(where)
        op.create_index(name, table, [_element(entry) for entry in columns], **kwargs)


def upgrade() -> None:
    for table in _CANONICAL_TABLES:
        op.add_column(
            table, sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True)
        )
        op.add_column(
            table, sa.Column("withdrawn_by_batch_id", sa.Uuid(as_uuid=True), nullable=True)
        )
        op.add_column(table, sa.Column("withdrawal_reason", sa.Text(), nullable=True))

    _rebuild_partial_indexes(_NEW_WHERE)

    op.create_table(
        _DECLARATIONS,
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("organization_id", sa.String(16), nullable=False),
        sa.Column("bank_id", sa.String(16), nullable=False),
        sa.Column("position_type", sa.String(32), nullable=False),
        sa.Column("source_system", sa.String(40), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("source_citation", sa.String(240), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("confirmation_status", sa.String(12), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("proposed_by", sa.String(120), nullable=False),
        sa.Column("proposed_by_user_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("proposed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_by", sa.String(120), nullable=True),
        sa.Column("approved_by_user_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.String(120), nullable=True),
        sa.Column("revocation_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('draft', 'approved')",
            name="ck_system_of_record_declarations_status",
        ),
        sa.CheckConstraint(
            "confirmation_status IN ('confirmed', 'pending')",
            name="ck_system_of_record_declarations_confirmation",
        ),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_system_of_record_declarations_window",
        ),
        sa.CheckConstraint(
            "length(trim(source_citation)) > 0",
            name="ck_system_of_record_declarations_citation_present",
        ),
        sa.CheckConstraint(
            "length(trim(rationale)) > 0",
            name="ck_system_of_record_declarations_rationale_present",
        ),
        # The database's half of four-eyes.
        sa.CheckConstraint(
            "status <> 'approved' OR (approved_by IS NOT NULL AND approved_at IS NOT NULL)",
            name="ck_system_of_record_declarations_approver_present",
        ),
        sa.ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["banks.id", "banks.organization_id"],
        ),
        sa.UniqueConstraint(
            "organization_id",
            "bank_id",
            "position_type",
            "effective_from",
            name="uq_system_of_record_declarations_generation",
        ),
    )
    op.create_index(
        "ix_system_of_record_declarations_resolution",
        _DECLARATIONS,
        ["organization_id", "bank_id", "position_type", "effective_from"],
    )

    op.create_table(
        _WITHDRAWALS,
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("organization_id", sa.String(16), nullable=False),
        sa.Column("bank_id", sa.String(16), nullable=False),
        sa.Column("source_system", sa.String(40), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("entity", sa.String(32), nullable=False),
        sa.Column("position_type", sa.String(32), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("declaration_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("requested_by", sa.String(120), nullable=False),
        sa.Column("requested_by_user_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_by", sa.String(120), nullable=True),
        sa.Column("approved_by_user_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("withdrawal_batch_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("rows_withdrawn", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("reversed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reversed_by", sa.String(120), nullable=True),
        sa.Column("reversed_by_user_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("reversal_reason", sa.Text(), nullable=True),
        sa.Column("reversal_batch_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("rows_restored", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "entity IN ('position', 'gl_account', 'counterparty', 'product')",
            name="ck_canonical_withdrawals_entity",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'applied', 'reversed')",
            name="ck_canonical_withdrawals_status",
        ),
        # A withdrawal without a reason cannot exist, at any layer...
        sa.CheckConstraint(
            "length(trim(reason)) > 0",
            name="ck_canonical_withdrawals_reason_present",
        ),
        # ...and neither can one that took effect without a named approver.
        sa.CheckConstraint(
            "status = 'pending' OR (approved_by IS NOT NULL AND approved_at IS NOT NULL)",
            name="ck_canonical_withdrawals_approver_present",
        ),
        sa.CheckConstraint(
            "status <> 'applied' OR withdrawal_batch_id IS NOT NULL",
            name="ck_canonical_withdrawals_batch_present",
        ),
        sa.CheckConstraint(
            "status <> 'reversed' OR "
            "(reversed_by IS NOT NULL AND reversed_at IS NOT NULL "
            "AND length(trim(coalesce(reversal_reason, ''))) > 0)",
            name="ck_canonical_withdrawals_reversal_evidence",
        ),
        sa.ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["banks.id", "banks.organization_id"],
        ),
    )
    op.create_index(
        "ix_canonical_withdrawals_scope",
        _WITHDRAWALS,
        ["organization_id", "bank_id", "as_of_date", "source_system", "entity"],
    )

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # The source_system / position_type CHECKs are generated from the
        # ingestion constants in the models; expressing the enum lists twice
        # invites drift, so they are declared there and applied here from the
        # same tuples.
        from app.domain.ingestion.constants import (  # noqa: PLC0415
            POSITION_TYPES,
            SOURCE_SYSTEMS,
        )

        sources = ", ".join(f"'{value}'" for value in SOURCE_SYSTEMS)
        types = ", ".join(f"'{value}'" for value in POSITION_TYPES)
        op.execute(
            f"ALTER TABLE {_DECLARATIONS} ADD CONSTRAINT "
            f"ck_system_of_record_declarations_source_system CHECK (source_system IN ({sources}))"
        )
        op.execute(
            f"ALTER TABLE {_DECLARATIONS} ADD CONSTRAINT "
            f"ck_system_of_record_declarations_position_type CHECK (position_type IN ({types}))"
        )
        op.execute(
            f"ALTER TABLE {_WITHDRAWALS} ADD CONSTRAINT "
            f"ck_canonical_withdrawals_source_system CHECK (source_system IN ({sources}))"
        )
        op.execute(
            f"ALTER TABLE {_WITHDRAWALS} ADD CONSTRAINT "
            f"ck_canonical_withdrawals_position_type CHECK "
            f"(position_type IS NULL OR position_type IN ({types}))"
        )
        for table in (_DECLARATIONS, _WITHDRAWALS):
            op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
            op.execute(
                f"""
                CREATE POLICY {table}_tenant_isolation ON {table}
                FOR ALL
                USING ((organization_id)::text = {_TENANT_ID_EXPR})
                WITH CHECK ((organization_id)::text = {_TENANT_ID_EXPR})
                """
            )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for table in (_DECLARATIONS, _WITHDRAWALS):
            op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
            op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_canonical_withdrawals_scope", table_name=_WITHDRAWALS)
    op.drop_table(_WITHDRAWALS)
    op.drop_index("ix_system_of_record_declarations_resolution", table_name=_DECLARATIONS)
    op.drop_table(_DECLARATIONS)

    _rebuild_partial_indexes(_OLD_WHERE)

    for table in _CANONICAL_TABLES:
        op.drop_column(table, "withdrawal_reason")
        op.drop_column(table, "withdrawn_by_batch_id")
        op.drop_column(table, "withdrawn_at")
