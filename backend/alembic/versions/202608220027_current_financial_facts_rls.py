"""Tenant isolation for ``current_financial_facts`` (P0-1).

``current_financial_facts`` is the live Treasury plane's current fact set — every
tenant's balance-sheet, capital, liquidity, IRR, FX and FTP inputs live in it.
It was created by ``202608190021`` WITHOUT row-level security, so it was the one
tenancy-carrying fact table on the primary reading
``relrowsecurity = relforcerowsecurity = false`` while every sibling
(``bank_financial_facts``, ``live_metrics``, ``live_metric_snapshots``,
``live_findings``, ``canonical_positions``, ``bank_reporting_periods``) is
FORCE-RLS. This migration closes that gap; it adds no column and rewrites no row,
so no calculation input, hash or output changes.

FORCE (not merely ENABLE) is required because the tenant application role owns
these tables: without it Postgres exempts the owner from its own policy.

Every code path that touches the table already runs on a session that sets the
``app.organization_id`` GUC (``app/api/deps.py::get_tenant_db_session`` for the
API, ``app/worker.py::_new_session`` for ``pipeline_refresh``/``official_run``,
``app/services/history_loader.py`` for bulk loads) and already filters by
``organization_id`` in SQL, so the policy is a second wall behind an existing
one — not a new access contract.

Revision ID: 202608220027
Revises: 202608210026
"""

from __future__ import annotations

from alembic import op

revision = "202608220027"
down_revision = "202608210026"
branch_labels = None
depends_on = None

# Post-platform-ID-epoch policy form: text comparison, never a ::uuid cast.
# ``organization_id`` is the OR-XXXXXXXX platform id (varchar), and an unset GUC
# yields NULL here so the predicate fails closed (no rows) rather than open.
_TENANT_ID_EXPR = "NULLIF(current_setting('app.organization_id', true), '')"
_TABLE = "current_financial_facts"


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        # SQLite (the hermetic suite) has no RLS; the DDL below is invalid there.
        return
    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY {_TABLE}_tenant_isolation ON {_TABLE}
        FOR ALL
        USING ((organization_id)::text = {_TENANT_ID_EXPR})
        WITH CHECK ((organization_id)::text = {_TENANT_ID_EXPR})
        """
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(f"DROP POLICY IF EXISTS {_TABLE}_tenant_isolation ON {_TABLE}")
    op.execute(f"ALTER TABLE {_TABLE} NO FORCE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_TABLE} DISABLE ROW LEVEL SECURITY")
