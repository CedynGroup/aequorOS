"""Every ``organization_id`` table is FORCE-RLS, or is documented as an exception.

The defect this exists for is not a missing policy on one table. It is that P0-1
was closed **per table**: ``202608220027`` gave ``current_financial_facts`` a
policy because the audit named it, and nothing was left behind that could find
the next one. Two more (``implied_rating_runs``, ``market_data_entitlements``)
were sitting unprotected on the primary at the time, and were only found by a
second human reading ``pg_class`` by hand.

This is the rule that replaces that reading. It asks Postgres — after the real
migration chain has run — for every table carrying ``organization_id`` and
requires each one to be BOTH ``relrowsecurity`` and ``relforcerowsecurity`` with
at least one policy, unless it is named in :data:`CROSS_TENANT_BY_DESIGN` with a
reason. A new tenant table that ships without RLS fails here by name.

**Why FORCE and not merely ENABLE.** The tenant application role OWNS these
tables, and Postgres exempts a table's owner from its own policies unless FORCE
is set. ``ENABLE`` alone on an owned table is decoration.

**Why against a migrated database and not the ORM metadata.** RLS is not
expressible in SQLAlchemy metadata; it exists only as DDL inside migrations, and
several migrations apply it through a loop over a module-local list. A static
scan of the migration text cannot resolve those, and a scan that silently misses
them is worse than none — it is the same false assurance that let this defect
survive. So the check runs where the truth is.

**Why ``bank_id`` is asked about too (2026-08-22).** The original census counted
``organization_id`` and nothing else, which assumes without checking that it is
the complete tenant marker. Measured read-only on the primary the same day, it
is: of 139 public tables, 123 carry ``organization_id``, **0 carry ``bank_id``
without it**, and the remaining 16 are the global registries (``jurisdictions``,
``institution_types``, ``regulatory_parameter``), the desk-as-vendor tables
(``desk_*``), the staff plane (``operator_users``, ``operator_audit_log``),
``organizations`` itself (already FORCE-RLS), ``platform_id_legacy_map``,
``worker_heartbeats`` and ``alembic_version`` — none of which carries a tenant
FK under any other name. So the rule below asks for BOTH markers: the
``bank_id``-only arm matches nothing today and exists so that the first table
scoped the other way fails here instead of shipping unnoticed, which is exactly
how the two tables ``202608230036`` had to close got in.

Postgres-gated: it needs ``TEST_DATABASE_URL``. The hermetic SQLite suite has no
row-level security at all, so there is nothing there to assert.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text

from tests.db.test_postgres_migrations import MigratedPostgresSchema, migrated_postgres_schema

__all__ = ["migrated_postgres_schema"]  # re-exported fixture

#: Tables that carry ``organization_id`` and are deliberately NOT tenant-isolated.
#: Every entry states WHY, and adding one is a decision, not a formality. Three
#: kinds qualify, and nothing else does:
#:
#: * a table read BEFORE a tenant is known (there is no GUC to compare against);
#: * a table belonging to the STAFF control plane, which is cross-tenant by
#:   definition and is never mounted on the tenant API;
#: * a table the tenant plane cannot reach at all.
CROSS_TENANT_BY_DESIGN: dict[str, str] = {
    "integration_keys": (
        "Read PRE-AUTH: the bearer credential is resolved by a global SHA-256 hash "
        "lookup before any organization is known, so there is no app.organization_id "
        "to compare against. Holds hashes and metadata only, and every endpoint that "
        "consumes it filters by org (CLAUDE.md pins this)."
    ),
    "operator_inspector_sessions": (
        "Staff control plane. The operator API is a separate entrypoint running a "
        "cross-tenant BYPASSRLS session by design, and a route-isolation test pins "
        "that it is never mounted on the tenant API."
    ),
    "tenant_storage": (
        "Staff control plane: the provisioning saga's per-tenant bucket registry, "
        "written and read only through the operator entrypoint (app/operator/), "
        "never by tenant-facing code."
    ),
}


#: The columns that make a table tenant data. ``organization_id`` is the tenant
#: key and the RLS predicate; ``bank_id`` is asked about separately because a
#: table scoped ONLY by bank would be tenant data the census never counted.
TENANT_MARKERS: tuple[str, ...] = ("organization_id", "bank_id")

_TENANT_TABLE_CENSUS = """
    SELECT c.relname,
           c.relrowsecurity,
           c.relforcerowsecurity,
           (SELECT count(*) FROM pg_policies p
             WHERE p.schemaname = n.nspname AND p.tablename = c.relname) AS policies,
           (SELECT string_agg(col.column_name, ',' ORDER BY col.column_name)
              FROM information_schema.columns col
             WHERE col.table_schema = n.nspname
               AND col.table_name = c.relname
               AND col.column_name = ANY(:markers)) AS markers
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = :schema_name
      AND c.relkind = 'r'
      AND EXISTS (
          SELECT 1 FROM information_schema.columns col
          WHERE col.table_schema = n.nspname
            AND col.table_name = c.relname
            AND col.column_name = ANY(:markers)
      )
    ORDER BY c.relname
"""


@pytest.mark.skipif(
    os.getenv("TEST_DATABASE_URL") is None,
    reason="TEST_DATABASE_URL is required for the tenant-RLS completeness check.",
)
def test_every_tenant_scoped_table_forces_row_level_security(
    migrated_postgres_schema: MigratedPostgresSchema,
) -> None:
    with migrated_postgres_schema.app_engine.connect() as connection:
        rows = connection.execute(
            text(_TENANT_TABLE_CENSUS),
            {
                "schema_name": migrated_postgres_schema.schema_name,
                "markers": list(TENANT_MARKERS),
            },
        ).all()

    assert rows, "no tenant-marked tables found — the query, not the schema, is wrong"
    assert any("organization_id" in (row.markers or "") for row in rows), (
        "not one table carries organization_id; the migration chain did not run"
    )

    unprotected = [
        f"{row.relname} (markers={row.markers}, rowsecurity={row.relrowsecurity}, "
        f"forced={row.relforcerowsecurity}, policies={row.policies})"
        for row in rows
        if row.relname not in CROSS_TENANT_BY_DESIGN
        and not (row.relrowsecurity and row.relforcerowsecurity and row.policies)
    ]
    assert not unprotected, (
        "these tables carry a tenant marker but do not FORCE row-level security. "
        "Add a policy in a migration (see 202608230036 for the shape), or — if the "
        "table is genuinely cross-tenant — add it to CROSS_TENANT_BY_DESIGN with the "
        "reason: " + "; ".join(unprotected)
    )


@pytest.mark.skipif(
    os.getenv("TEST_DATABASE_URL") is None,
    reason="TEST_DATABASE_URL is required for the tenant-RLS completeness check.",
)
def test_organization_id_is_still_the_complete_tenant_marker(
    migrated_postgres_schema: MigratedPostgresSchema,
) -> None:
    """No table is scoped by ``bank_id`` alone.

    The whole census rests on ``organization_id`` being THE tenant key: it is
    the RLS predicate, the JWT ``org`` claim and the ``app.organization_id`` GUC.
    A table carrying only ``bank_id`` would be tenant data that the predicate
    cannot express — the policy would have to join ``banks`` — so it is a design
    decision, not a detail, and it must be made deliberately rather than
    discovered later.
    """
    with migrated_postgres_schema.app_engine.connect() as connection:
        rows = connection.execute(
            text(_TENANT_TABLE_CENSUS),
            {
                "schema_name": migrated_postgres_schema.schema_name,
                "markers": list(TENANT_MARKERS),
            },
        ).all()

    bank_only = sorted(row.relname for row in rows if row.markers == "bank_id")
    assert not bank_only, (
        "these tables carry bank_id but no organization_id, so the tenant RLS "
        "predicate cannot be written over them directly. Add organization_id (the "
        "platform tenant key) and a policy, or document the table in "
        f"CROSS_TENANT_BY_DESIGN with the reason: {bank_only}"
    )


@pytest.mark.skipif(
    os.getenv("TEST_DATABASE_URL") is None,
    reason="TEST_DATABASE_URL is required for the tenant-RLS completeness check.",
)
def test_the_documented_exceptions_are_all_real_and_still_unprotected(
    migrated_postgres_schema: MigratedPostgresSchema,
) -> None:
    """The allow-list may not rot into a place tables are parked.

    An entry that no longer names a real table, or that names one which has since
    been given a policy, is a stale exemption — and a stale exemption is how the
    next table gets waved through.
    """
    with migrated_postgres_schema.app_engine.connect() as connection:
        state: dict[str, bool] = {
            str(name): bool(forced)
            for name, forced in connection.execute(
                text(
                    """
                    SELECT c.relname, c.relforcerowsecurity
                    FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = :schema_name AND c.relkind = 'r'
                    """
                ),
                {"schema_name": migrated_postgres_schema.schema_name},
            ).all()
        }

    missing = sorted(name for name in CROSS_TENANT_BY_DESIGN if name not in state)
    assert not missing, f"CROSS_TENANT_BY_DESIGN names tables that do not exist: {missing}"

    now_protected = sorted(name for name in CROSS_TENANT_BY_DESIGN if state[name])
    assert not now_protected, (
        "these tables are now FORCE-RLS, so their exemption is stale and must be "
        f"deleted from CROSS_TENANT_BY_DESIGN: {now_protected}"
    )
