"""canonical positions blotter indexes

Revision ID: 202607170008
Revises: 202607170007
Create Date: 2026-07-17 23:30:00.000000

Server pagination for the positions blotter reads the current generation of
``canonical_positions`` ordered by (source_reference, id) and counts it under
type/currency filters. On a 418k-position book the ordered page read was a
full parallel seq scan plus top-N sort per page turn (measured >2s under
memory pressure), so two partial indexes over the current generation
(``superseded_by IS NULL``) make it index work:

- ``(organization_id, bank_id, source_reference, id) INCLUDE (position_type,
  currency)`` serves ordered page windows and reference-search scans with
  early termination; the INCLUDE payload keeps type/currency-filtered ordered
  scans index-only instead of heap-fetching every candidate row.
- ``(organization_id, bank_id, position_type, currency)`` serves filtered
  counts and the type/currency facet rollups as index-only scans.

Index-only migration: no table shape, RLS, or policy changes. ``downgrade``
drops both indexes.
"""

import sqlalchemy as sa

from alembic import op

revision = "202607170008"
down_revision = "202607170007"
branch_labels = None
depends_on = None

TABLE = "canonical_positions"
CURRENT_GENERATION = sa.text("superseded_by IS NULL")


def upgrade() -> None:
    op.create_index(
        "ix_canonical_positions_current_org_bank_ref",
        TABLE,
        ["organization_id", "bank_id", "source_reference", "id"],
        postgresql_include=["position_type", "currency"],
        postgresql_where=CURRENT_GENERATION,
        sqlite_where=CURRENT_GENERATION,
    )
    op.create_index(
        "ix_canonical_positions_current_org_bank_type",
        TABLE,
        ["organization_id", "bank_id", "position_type", "currency"],
        postgresql_where=CURRENT_GENERATION,
        sqlite_where=CURRENT_GENERATION,
    )


def downgrade() -> None:
    op.drop_index("ix_canonical_positions_current_org_bank_type", table_name=TABLE)
    op.drop_index("ix_canonical_positions_current_org_bank_ref", table_name=TABLE)
