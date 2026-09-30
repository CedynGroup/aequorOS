"""GL by branch: the ``gl_segment_balances`` dataset and its branch mart.

Revision ID: 202609280075
Revises: 202609270074

``docs/bi.md`` §Phase 5 left the design open — "GL by branch needs segmented GL
codes or a new ``gl_segment_balances`` dataset" — and this revision records the
answer: **a dataset, not segmented codes.** The argument is in
``docs/data_engine/datasets/gl_segment_balances.md``; the part that settles it is
GRAIN. ``canonical_gl_accounts`` is one row per ``(account_code, as_of_date)`` and
``uq_canonical_gl_accounts_current`` forbids a second key there, while branch
profit-and-loss is ``(account, branch, month)``. A position is individually
identified, so its branch is an ATTRIBUTE; an account is identified by its code,
so its branch is a second KEY. Segmented codes evade that index only by asserting
that one branch's interest income is a different ACCOUNT from another's, which it
is not. Parsing a place out of ``account_code`` would also be the third instance
of a mistake this platform has already ruled against twice (region is declared and
never inferred; the ``"bog"`` substring test that dropped a tenant's central-bank
balances out of HQLA) — and aggravated, because ``gl_mapping_bsd7`` ALREADY parses
that same string by longest prefix to choose a filed BSD7 line.

Three changes, one feature.

1. ``ck_canonical_reference_rows_dataset_kind`` admits ``gl_segment_balances``. A
   bank's branch breakdown of its ledger is a reference dataset like every other
   bank-supplied register: rows preserved verbatim with full batch lineage, never
   seeded.
2. ``bi_fact_gl_branch_monthly``, the branch mart. **A SEPARATE table rather than a
   ``branch_code`` column on ``bi_fact_gl_monthly``**, and that is a security
   decision rather than a modelling preference:
   ``services/bi/authorization.branch_attributable`` reads the branch key off the
   MAPPED TABLE, so a branch key on the institution's own ledger would have made
   the institution's whole profit-and-loss readable by a branch-scoped principal —
   the exact disclosure Phase 4's data scopes exist to prevent.
3. ``ck_bi_reconciliation_results_check_id`` admits ``R11``, the branch identity:
   the sum of branch ``ytd_rc`` for an account equals the institution's row,
   exactly. ``app/models/bi.RECONCILIATION_CHECK_IDS`` already names it, so the
   hermetic suite is green, but a Postgres database still on ``202609220066``
   refuses an ``R11`` row until this lands. ``reconciliation.persist`` logs the
   skip rather than failing the build, which is the documented degradation.

Not partitioned, deliberately: the grain is one row per (account, branch, month),
so a bank with 400 profit-and-loss accounts and 60 branches writes about 24,000
rows a month, against the position facts' millions a day. Partitioning would add
the DEFAULT-partition and child-RLS obligations for no volume.

The BEFORE lists are written out rather than derived from the model constants, as
``202609220068`` established: a literal keeps a fresh upgrade and an
already-migrated database on the same constraint after the NEXT kind or check is
added, and it lets the downgrade restore the historical shape. Parity between the
constants and the migrated CHECKs is asserted instead, by
``tests/db/test_bi_migration_structural_parity.py`` and
``tests/models/test_bi_models.py``.

Widening moves no data. NARROWING does, so ``downgrade`` deletes the rows that
cannot satisfy the older constraints first — and those DELETEs run under
``force_rls_suspended``, because alembic runs as the tenant-scoped app role and
would otherwise match zero rows in every tenant and report success, leaving
exactly the rows that then make the constraint fail to apply. That is the
``202608150013`` lesson.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.db.session import force_rls_suspended

revision = "202609280075"
down_revision = "202609270074"
branch_labels = None
depends_on = None

_TENANT_ID_EXPR = "NULLIF(current_setting('app.organization_id', true), '')"

_REF_TABLE = "canonical_reference_rows"
_REF_CONSTRAINT = "ck_canonical_reference_rows_dataset_kind"
_NEW_KIND = "gl_segment_balances"
_KINDS_BEFORE = (
    "'capital_structure', 'behavioral_assumptions', 'yield_curve', 'fx_rates_current', "
    "'fx_rates_historical', 'historical_cashflows', 'historical_financials', "
    "'business_units', 'institution', 'gl_mapping_bsd7', 'subsidiaries', "
    "'tariff_schedule', 'capital_expenditure', 'atm_operations', 'remittance_flows', "
    "'teller_withdrawals', 'interest_accruals', 'performance_targets'"
)
_KINDS_AFTER = f"{_KINDS_BEFORE}, '{_NEW_KIND}'"

_RECON_TABLE = "bi_reconciliation_results"
_RECON_CONSTRAINT = "ck_bi_reconciliation_results_check_id"
_CHECKS_BEFORE = "'R1', 'R2', 'R3', 'R4', 'R5', 'R6', 'R7', 'R8', 'R9', 'R10'"
_CHECKS_AFTER = f"{_CHECKS_BEFORE}, 'R11'"

_MART = "bi_fact_gl_branch_monthly"
_MART_INDEX = f"ix_{_MART}_org_bank_month_branch"
_BALANCE_BASES = "'ytd', 'period'"


def upgrade() -> None:
    with op.batch_alter_table(_REF_TABLE) as batch:
        batch.drop_constraint(_REF_CONSTRAINT, type_="check")
        batch.create_check_constraint(_REF_CONSTRAINT, f"dataset_kind IN ({_KINDS_AFTER})")

    op.create_table(
        _MART,
        sa.Column("organization_id", sa.String(length=16), nullable=False),
        sa.Column("bank_id", sa.String(length=16), nullable=False),
        sa.Column("month_end", sa.Date(), nullable=False),
        sa.Column("gl_account_code", sa.String(length=80), nullable=False),
        # 120 matches ``bi_dim_branch.branch_code`` and the position facts. A
        # narrower column would fail a tenant's WHOLE nightly build on one long
        # branch id, which is exactly how a four-character shortfall once did.
        sa.Column("branch_code", sa.String(length=120), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("calendar_month", sa.Date(), nullable=False),
        sa.Column("account_class", sa.String(length=16), nullable=False),
        sa.Column("ytd_rc", sa.Numeric(28, 6), nullable=False),
        # NULLABLE on purpose, all three. A branch with no prior month has an
        # UNKNOWN movement, not a zero one, and ``missing_prior`` is what says so.
        sa.Column("prior_ytd_rc", sa.Numeric(28, 6), nullable=True),
        sa.Column("movement_rc", sa.Numeric(28, 6), nullable=True),
        sa.Column("missing_prior", sa.Boolean(), nullable=False),
        sa.Column("balance_basis", sa.String(length=16), nullable=False),
        sa.Column("pl_line", sa.String(length=80), nullable=True),
        sa.Column("pl_sign", sa.SmallInteger(), nullable=True),
        sa.Column("register_as_of", sa.Date(), nullable=False),
        sa.Column("builder_version", sa.Integer(), nullable=False),
        sa.Column("built_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "organization_id",
            "bank_id",
            "month_end",
            "gl_account_code",
            "branch_code",
            "currency",
            name=f"pk_{_MART}",
        ),
        sa.CheckConstraint(
            f"balance_basis IN ({_BALANCE_BASES})", name=f"ck_{_MART}_balance_basis"
        ),
        sa.ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["banks.id", "banks.organization_id"],
            name=f"fk_{_MART}_bank",
        ),
    )
    op.create_index(
        _MART_INDEX,
        _MART,
        ["organization_id", "bank_id", "month_end", "branch_code"],
    )
    # Postgres does not inherit RLS, and this table has no children, so the trio
    # here is the whole story. FORCE matters even so: without it the table owner
    # reads every tenant.
    op.execute(f"ALTER TABLE {_MART} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_MART} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {_MART}_tenant_isolation ON {_MART} FOR ALL "
        f"USING ((organization_id)::text = {_TENANT_ID_EXPR}) "
        f"WITH CHECK ((organization_id)::text = {_TENANT_ID_EXPR})"
    )

    with op.batch_alter_table(_RECON_TABLE) as batch:
        batch.drop_constraint(_RECON_CONSTRAINT, type_="check")
        batch.create_check_constraint(_RECON_CONSTRAINT, f"check_id IN ({_CHECKS_AFTER})")


def downgrade() -> None:
    with force_rls_suspended(op.get_bind(), _RECON_TABLE):
        op.execute(f"DELETE FROM {_RECON_TABLE} WHERE check_id = 'R11'")
    with op.batch_alter_table(_RECON_TABLE) as batch:
        batch.drop_constraint(_RECON_CONSTRAINT, type_="check")
        batch.create_check_constraint(_RECON_CONSTRAINT, f"check_id IN ({_CHECKS_BEFORE})")

    op.drop_index(_MART_INDEX, table_name=_MART)
    op.drop_table(_MART)

    # A downgrade past this revision DISCARDS whatever branch breakdowns a tenant
    # had pushed. Not routine, and the bank would have to push them again.
    with force_rls_suspended(op.get_bind(), _REF_TABLE):
        op.execute(f"DELETE FROM {_REF_TABLE} WHERE dataset_kind = '{_NEW_KIND}'")
    with op.batch_alter_table(_REF_TABLE) as batch:
        batch.drop_constraint(_REF_CONSTRAINT, type_="check")
        batch.create_check_constraint(_REF_CONSTRAINT, f"dataset_kind IN ({_KINDS_BEFORE})")
