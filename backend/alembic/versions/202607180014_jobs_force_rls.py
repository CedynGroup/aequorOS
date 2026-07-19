"""Re-assert FORCE ROW LEVEL SECURITY on jobs.

``202605250002`` enabled + forced RLS on ``jobs``, but the live database
drifted: ``relforcerowsecurity`` is off (almost certainly a manual ``NO FORCE``
applied while the background worker still ran on the tenant app role, before
the BYPASSRLS worker role existed). Because the app role owns the table,
un-forced RLS does not apply to it at all — it could read every tenant's job
rows without an organization context, violating the tenant-isolation invariant
(the API filters explicitly, so this was defense-in-depth, not an open door).

The worker now runs on a BYPASSRLS role (``WORKER_DATABASE_URL``), so nothing
legitimate depends on the drift. Re-assert FORCE; idempotent if already set.

Revision ID: 202607180014
Revises: 202607180013
"""

from __future__ import annotations

from alembic import op

revision = "202607180014"
down_revision = "202607180013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("ALTER TABLE jobs FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("ALTER TABLE jobs NO FORCE ROW LEVEL SECURITY")
