"""Scope mapping configs to a specific data source via ``source_ref``.

Two sources of the same ``source_system`` at one bank (e.g. an Oracle FLEXCUBE
core and a Snowflake warehouse, both ``DB_DIRECT``) must each carry their own
mapping. ``source_ref`` (a source instance id, or ``''`` for source-system-wide)
is added to the scope, so the version-uniqueness and single-active constraints
key on it. Existing rows default to ``''`` (source-system-wide), unchanged.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202607180012"
down_revision = "202607180011"
branch_labels = None
depends_on = None

TABLE = "mapping_configs"
_SCOPE_VERSION = "uq_mapping_configs_scope_version"
_SINGLE_ACTIVE = "uq_mapping_configs_single_active"


def upgrade() -> None:
    op.add_column(
        TABLE,
        sa.Column("source_ref", sa.String(length=255), nullable=False, server_default=""),
    )
    op.drop_constraint(_SCOPE_VERSION, TABLE, type_="unique")
    op.create_unique_constraint(
        _SCOPE_VERSION,
        TABLE,
        ["organization_id", "bank_id", "source_system", "source_ref", "version"],
    )
    op.drop_index(_SINGLE_ACTIVE, table_name=TABLE)
    op.create_index(
        _SINGLE_ACTIVE,
        TABLE,
        ["organization_id", "bank_id", "source_system", "source_ref"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
        sqlite_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    op.drop_index(_SINGLE_ACTIVE, table_name=TABLE)
    op.create_index(
        _SINGLE_ACTIVE,
        TABLE,
        ["organization_id", "bank_id", "source_system"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
        sqlite_where=sa.text("status = 'active'"),
    )
    op.drop_constraint(_SCOPE_VERSION, TABLE, type_="unique")
    op.create_unique_constraint(
        _SCOPE_VERSION, TABLE, ["organization_id", "bank_id", "source_system", "version"]
    )
    op.drop_column(TABLE, "source_ref")
