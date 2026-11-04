"""platform public IDs for organizations and banks

Revision ID: 202607240024
Revises: 202607240023

Tenant-facing identity: every organization and bank carries a short
platform-generated identifier (OR-XXXXXXXX / BK-XXXXXXXX, Crockford base32)
alongside its internal UUID primary key. Generated at row creation by the
model default for every creation path — sandbox and real tenants alike —
and backfilled here for existing rows. Globally unique; immutable by
convention (no update path exposes it).
"""

from __future__ import annotations

import secrets

import sqlalchemy as sa

from alembic import op

revision = "202607240024"
down_revision = "202607240023"
branch_labels = None
depends_on = None

# Frozen copy of app/services/public_ids.py at migration time — migrations
# stay self-contained so later app changes never alter this backfill.
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _generate(prefix: str) -> str:
    return f"{prefix}-" + "".join(secrets.choice(_CROCKFORD) for _ in range(8))


_TARGETS = (
    ("organizations", "OR"),
    ("banks", "BK"),
)


def upgrade() -> None:
    connection = op.get_bind()
    is_postgres = connection.dialect.name == "postgresql"
    for table, prefix in _TARGETS:
        # FORCE RLS hides other tenants' rows from the backfill UPDATE while
        # ALTER ... NOT NULL validates every row — lift FORCE for the owner
        # during the backfill and restore it before finishing (transactional
        # DDL keeps this atomic).
        forced_rls = False
        if is_postgres:
            forced_rls = bool(
                connection.execute(
                    sa.text(
                        "SELECT relforcerowsecurity FROM pg_class WHERE relname = :table"
                    ),
                    {"table": table},
                ).scalar()
            )
            if forced_rls:
                op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.add_column(table, sa.Column("public_id", sa.String(length=16), nullable=True))
        rows = connection.execute(
            sa.text(f"SELECT id FROM {table} WHERE public_id IS NULL")  # noqa: S608
        ).fetchall()
        assigned: set[str] = set()
        for (row_id,) in rows:
            code = _generate(prefix)
            while code in assigned:  # pragma: no cover - 32^8 collision space
                code = _generate(prefix)
            assigned.add(code)
            connection.execute(
                sa.text(f"UPDATE {table} SET public_id = :code WHERE id = :row_id"),  # noqa: S608
                {"code": code, "row_id": row_id},
            )
        with op.batch_alter_table(table) as batch:
            batch.alter_column("public_id", existing_type=sa.String(length=16), nullable=False)
        op.create_index(f"uq_{table}_public_id", table, ["public_id"], unique=True)
        if forced_rls:
            op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    for table, _ in reversed(_TARGETS):
        op.drop_index(f"uq_{table}_public_id", table_name=table)
        op.drop_column(table, "public_id")
