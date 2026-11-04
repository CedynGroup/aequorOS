"""Tenant isolation for ``implied_rating_runs`` and ``market_data_entitlements``.

P0-1 was fixed by NAME, not by rule. ``202608220027`` closed
``current_financial_facts`` — the one table the audit happened to name — and
nothing was built to find the next one. Measured read-only on the primary on
2026-08-22, five of the 123 ``organization_id``-bearing tables were neither
``relrowsecurity`` nor ``relforcerowsecurity``::

    implied_rating_runs          rowsecurity=False forced=False policies=0
    market_data_entitlements     rowsecurity=False forced=False policies=0
    integration_keys             rowsecurity=False forced=False policies=0   (by design)
    operator_inspector_sessions  rowsecurity=False forced=False policies=0   (by design)
    tenant_storage               rowsecurity=False forced=False policies=0   (by design)

This migration closes the two that are tenant data:

``implied_rating_runs`` (``202608110049``)
    One row per bank per reporting period carrying the implied credit rating,
    its notch score and the input hash that produced it — a supervisory
    assessment of the institution, and among the most sensitive rows the
    platform holds.

``market_data_entitlements`` (``202608110047``)
    Which desk datasets an organization is granted, effective-dated. A grant is
    a commercial fact about one tenant, and the read path
    (``market_desk.entitlements.active_datasets``) grandfathers an org with NO
    visible rows to the standard tier — so a leak in either direction changes
    what a tenant can see.

The three that remain outside are deliberate and are pinned as such by
``tests/db/test_tenant_rls_completeness.py``, which is the RULE this migration
ships with: it fails when ANY ``organization_id`` table is not FORCE-RLS and is
not on that documented list, so a sixth table cannot appear unnoticed.

The desk publish fan-out (``market_desk.publication.publish``) reads every
organization's entitlements in one loop. It already selects across ``banks``,
which has been FORCE-RLS since ``202605250002``, so it necessarily runs on a
BYPASSRLS session and is unaffected. FORCE (not merely ENABLE) is required
because the tenant application role owns these tables: without it Postgres
exempts the owner from its own policy.

No column is added and no row is rewritten, so no calculation input, hash or
output changes.

Revision ID: 202608230036
Revises: 202608230035
"""

from __future__ import annotations

from alembic import op

revision = "202608230036"
down_revision = "202608230035"
branch_labels = None
depends_on = None

# Post-platform-ID-epoch policy form: text comparison, never a ::uuid cast.
# ``organization_id`` is the OR-XXXXXXXX platform id (varchar), and an unset GUC
# yields NULL here so the predicate fails closed (no rows) rather than open.
_TENANT_ID_EXPR = "NULLIF(current_setting('app.organization_id', true), '')"
_TABLES = ("implied_rating_runs", "market_data_entitlements")


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        # SQLite (the hermetic suite) has no RLS; the DDL below is invalid there.
        return
    for table in _TABLES:
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
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in reversed(_TABLES):
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
