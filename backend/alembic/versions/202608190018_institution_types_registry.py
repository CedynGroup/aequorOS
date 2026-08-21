"""Institution-type registry: the typed discriminator every SDI scoping keys off.

Creates the global ``institution_types`` reference table (NOT tenant-scoped —
shared reference data like ``jurisdictions``, no RLS), seeds the seven BoG
licence classes with their derived regime attributes (docs/sdi.md §1), and adds
the ``banks.institution_type`` column that resolves through it — mirroring the
``jurisdictions`` registry + ``banks.jurisdiction_code`` pattern
(``202607230017``).

``banks.institution_type`` is REQUIRED with no default (the same fail-loud
discipline as ``currency``/``jurisdiction_code``), so existing rows are migrated
in a safe TWO STEP: add the column NULLABLE, backfill every existing bank to
``universal_bank`` (the as-built anchor — every live tenant today is a universal
bank), THEN promote the column to NOT NULL and attach the FK. ``banks`` is not
an RLS-forced table, so the backfill UPDATE runs correctly under the app role
(no BYPASSRLS role needed).

Phase A lays the discriminator + plumbing only. It changes NO capital/liquidity/
return behaviour and does NOT gate modules — ``default_modules`` is seeded as
data for later Phase-B module gating. The SDI return family (``return_family =
'sdi'``) is a classification here, not an invented BoG form pack (docs/sdi.md
§2.3 — obtain, never fabricate).

Revision ID: 202608190018
Revises: 202608160016
"""

from __future__ import annotations

from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op

revision = "202608190018"
# Chained off the head that existed when Phase A was scoped. A sibling migration
# (202608190017) also branches from 202608160016; the release orchestrator
# linearizes the two heads. Leave this down_revision as-is.
down_revision = "202608160016"
branch_labels = None
depends_on = None

TABLE = "institution_types"
FK = "fk_banks_institution_type_institution_types"

# The default scoped module sets (docs/sdi.md §3). Stored as DATA for later
# Phase-B gating; Phase A wires no gating. Module slugs are stable identifiers
# for the platform's top-level modules (route/nav keys).
_BANK_MODULES = [
    "command_center",
    "risk",
    "alerts",
    "liquidity",
    "capital",
    "regulatory_reporting",
    "data_engine",
    "institution",
    "reports",
    "settings",
    "irrbb",
    "behavioral",
    "forecasting",
    "ftp",
    "fx",
    "markets",
    "positions",
]
# The default SDI (micro) config: the bank-only Basel modules (FTP, Forecasting,
# IRRBB, FX, Markets, Positions, Behavioral) drop out (docs/sdi.md §3.2).
_SDI_MODULES = [
    "command_center",
    "risk",
    "alerts",
    "liquidity",
    "capital",
    "regulatory_reporting",
    "data_engine",
    "institution",
    "reports",
    "settings",
]

# type_code, display_name, class, return_family, capital_regime,
# large_exposure_limit_pct, single_obligor_limit_pct, liquidity_binding, modules
#
# Derivation (docs/sdi.md §1.1, §2.2): deposit-taking non-bank licences resolve
# to the 'sdi' regime (Act 930 s.29 capital floor, 15% large-exposure limit,
# binding LMTD Table 1 ratios); the universal bank and a banking-group financial
# holding company resolve to the 'bank' regime (Basel CRD, 20% limit, Table 1 as
# monitoring). ``financial_holding_company`` and ``other_rfi`` classifications
# are Phase-A defaults flagged for BoG confirmation (docs/sdi.md §8 item 5).
SEED_ROWS = [
    ("universal_bank", "Universal Bank", "bank", "bsd", "crd", 20, 25, False, _BANK_MODULES),
    ("savings_and_loans", "Savings & Loans", "sdi", "sdi", "s29", 15, 25, True, _SDI_MODULES),
    ("finance_house", "Finance House", "sdi", "sdi", "s29", 15, 25, True, _SDI_MODULES),
    (
        "rural_community_bank",
        "Rural & Community Bank",
        "sdi",
        "sdi",
        "s29",
        15,
        25,
        True,
        _SDI_MODULES,
    ),
    (
        "microfinance_bank",
        "Microfinance Institution",
        "sdi",
        "sdi",
        "s29",
        15,
        25,
        True,
        _SDI_MODULES,
    ),
    (
        "financial_holding_company",
        "Financial Holding Company",
        "bank",
        "bsd",
        "crd",
        20,
        25,
        False,
        _BANK_MODULES,
    ),
    (
        "other_rfi",
        "Other Regulated Financial Institution",
        "sdi",
        "sdi",
        "s29",
        15,
        25,
        True,
        _SDI_MODULES,
    ),
]


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("type_code", sa.String(length=40), nullable=False),
        sa.Column("display_name", sa.String(length=80), nullable=False),
        sa.Column("institution_class", sa.String(length=8), nullable=False),
        sa.Column("return_family", sa.String(length=8), nullable=False),
        sa.Column("capital_regime", sa.String(length=8), nullable=False),
        sa.Column("large_exposure_limit_pct", sa.Numeric(6, 3), nullable=False),
        sa.Column("single_obligor_limit_pct", sa.Numeric(6, 3), nullable=False),
        sa.Column("liquidity_binding", sa.Boolean(), nullable=False),
        sa.Column("default_modules", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("type_code"),
        sa.CheckConstraint(
            "institution_class IN ('bank', 'sdi')",
            name="ck_institution_types_institution_class",
        ),
        sa.CheckConstraint(
            "return_family IN ('bsd', 'sdi')",
            name="ck_institution_types_return_family",
        ),
        sa.CheckConstraint(
            "capital_regime IN ('crd', 's29')",
            name="ck_institution_types_capital_regime",
        ),
    )

    now = datetime.now(UTC)
    seed_table = sa.table(
        TABLE,
        sa.column("type_code", sa.String),
        sa.column("display_name", sa.String),
        sa.column("institution_class", sa.String),
        sa.column("return_family", sa.String),
        sa.column("capital_regime", sa.String),
        sa.column("large_exposure_limit_pct", sa.Numeric),
        sa.column("single_obligor_limit_pct", sa.Numeric),
        sa.column("liquidity_binding", sa.Boolean),
        sa.column("default_modules", sa.JSON),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    op.bulk_insert(
        seed_table,
        [
            {
                "type_code": code,
                "display_name": display_name,
                "institution_class": institution_class,
                "return_family": return_family,
                "capital_regime": capital_regime,
                "large_exposure_limit_pct": large_exposure,
                "single_obligor_limit_pct": single_obligor,
                "liquidity_binding": liquidity_binding,
                "default_modules": modules,
                "created_at": now,
                "updated_at": now,
            }
            for (
                code,
                display_name,
                institution_class,
                return_family,
                capital_regime,
                large_exposure,
                single_obligor,
                liquidity_binding,
                modules,
            ) in SEED_ROWS
        ],
    )

    # Two-step backfill: NULLABLE add -> backfill existing banks -> NOT NULL + FK.
    op.add_column(
        "banks",
        sa.Column("institution_type", sa.String(length=40), nullable=True),
    )
    # A production migration connection can be subject to the bank table's
    # tenant policy. DDL sees every row, but the UPDATE may otherwise see none
    # and the subsequent NOT NULL promotion fails. Preserve the prior RLS state
    # and disable it only for this global, deterministic backfill.
    connection = op.get_bind()
    rls_state = None
    if connection.dialect.name == "postgresql":
        rls_state = connection.execute(
            sa.text(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE oid = 'banks'::regclass"
            )
        ).one()
        if rls_state.relrowsecurity:
            op.execute("ALTER TABLE banks DISABLE ROW LEVEL SECURITY")
    op.execute(
        "UPDATE banks SET institution_type = 'universal_bank' "
        "WHERE institution_type IS NULL"
    )
    if rls_state is not None and rls_state.relrowsecurity:
        op.execute("ALTER TABLE banks ENABLE ROW LEVEL SECURITY")
        if rls_state.relforcerowsecurity:
            op.execute("ALTER TABLE banks FORCE ROW LEVEL SECURITY")
    op.alter_column("banks", "institution_type", nullable=False)
    op.create_foreign_key(FK, "banks", TABLE, ["institution_type"], ["type_code"])


def downgrade() -> None:
    op.drop_constraint(FK, "banks", type_="foreignkey")
    op.drop_column("banks", "institution_type")
    op.drop_table(TABLE)
