"""Allow the ``snowflake`` backend on database-direct connections.

Snowflake is added as a database-direct backend (data_engine.md §11.3), so the
``backend`` CHECK constraint must accept it. Postgres has no in-place edit for a
CHECK constraint, so this drops and recreates it with the widened value set.
"""

from __future__ import annotations

from alembic import op

revision = "202607180011"
down_revision = "202607170010"
branch_labels = None
depends_on = None

TABLE = "database_direct_connections"
CONSTRAINT = "ck_database_direct_connections_backend"
_OLD_BACKENDS = "'oracle', 'sqlserver', 'jdbc', 'odbc'"
_NEW_BACKENDS = "'oracle', 'sqlserver', 'jdbc', 'odbc', 'snowflake'"


def _recreate_backend_check(values: str) -> None:
    op.drop_constraint(CONSTRAINT, TABLE, type_="check")
    op.create_check_constraint(CONSTRAINT, TABLE, f"backend IN ({values})")


def upgrade() -> None:
    _recreate_backend_check(_NEW_BACKENDS)


def downgrade() -> None:
    _recreate_backend_check(_OLD_BACKENDS)
