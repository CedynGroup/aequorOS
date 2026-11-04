"""LMTD/LRMD 2026 canonical foundation: encumbrance + liquidity classifiers

Revision ID: 202608070031
Revises: 202607260030

The BoG Liquidity Monitoring Tools Directive and Liquidity Risk Management
Directive (both effective 2027-01-01, bank alignment due 2026-12-31) require
classifications the canonical model honestly did not carry — the gap that
left seven of eleven LMTD appendix tables unfilled (docs/lmtd_gap_analysis.md
§6, docs/lrmd_gap_analysis.md §3).

``canonical_position_snapshots`` gains nine nullable columns:

- ``encumbered`` + ``encumbrance_reason`` — the single highest-leverage
  addition (LMTD Tables 1/4/9/10; LRMD ¶64–69 mandates the distinction and
  ¶65 excludes pledged assets from the liquid stock);
- ``owning_entity`` + ``asset_location`` — LRMD ¶64 collateral management;
  ``asset_location`` is LMTD Table 9's "Location" column;
- ``operational_purpose`` + ``redeemable_within_two_days`` — LMTD ¶5 Narrow
  Liquid Assets legs (b) and (f);
- ``pledged_as_collateral`` + ``lien_reference`` — LMTD ¶23's netting rule
  (pledged deposits deducted from both sides of the concentration metrics);
- ``deposit_account_type`` (CHECK: CURRENT/CALL/SAVINGS/FIXED/OTHER) — drives
  the volatile-liability definition (current + call) and the
  contractual-by-nature <1yr rule without product-code heuristics.

``canonical_counterparties`` gains ``resident`` (nullable boolean) — the
resident/non-resident marker two Narrow Liquid Assets legs classify on.

Everything is nullable and consumers treat NULL conservatively, so existing
returns are byte-identical until a source actually supplies the fields. No
backfill: inventing classifications for ingested history would violate the
provenance discipline — corrections arrive as new generations via ingestion
or audited HUMAN_OVERRIDE, never as migration fiat.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202608070031"
down_revision = "202607260030"
branch_labels = None
depends_on = None

_SNAPSHOT_COLUMNS: tuple[sa.Column, ...] = (
    sa.Column("encumbered", sa.Boolean(), nullable=True),
    sa.Column("encumbrance_reason", sa.String(length=120), nullable=True),
    sa.Column("owning_entity", sa.String(length=120), nullable=True),
    sa.Column("asset_location", sa.String(length=120), nullable=True),
    sa.Column("operational_purpose", sa.Boolean(), nullable=True),
    sa.Column("redeemable_within_two_days", sa.Boolean(), nullable=True),
    sa.Column("pledged_as_collateral", sa.Boolean(), nullable=True),
    sa.Column("lien_reference", sa.String(length=255), nullable=True),
    sa.Column("deposit_account_type", sa.String(length=16), nullable=True),
)

_DEPOSIT_TYPE_CHECK = (
    "deposit_account_type IN ('CURRENT', 'CALL', 'SAVINGS', 'FIXED', 'OTHER') "
    "OR deposit_account_type IS NULL"
)


def upgrade() -> None:
    for column in _SNAPSHOT_COLUMNS:
        op.add_column("canonical_position_snapshots", column)
    # batch_alter_table because the hermetic test database is SQLite, which
    # cannot add a table CHECK constraint in place.
    with op.batch_alter_table("canonical_position_snapshots") as batch:
        batch.create_check_constraint(
            "ck_canonical_position_snapshots_deposit_account_type",
            _DEPOSIT_TYPE_CHECK,
        )
    op.add_column("canonical_counterparties", sa.Column("resident", sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column("canonical_counterparties", "resident")
    with op.batch_alter_table("canonical_position_snapshots") as batch:
        batch.drop_constraint(
            "ck_canonical_position_snapshots_deposit_account_type", type_="check"
        )
    for column in reversed(_SNAPSHOT_COLUMNS):
        op.drop_column("canonical_position_snapshots", column.name)
