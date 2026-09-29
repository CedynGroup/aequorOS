"""An existing child is VERIFIED before it is handed back, never adopted on its name.

Revision ID: 202609290079
Revises: 202609290078

Audit A360 finding H6, reproduced end to end on PostgreSQL 16.

``bi_ensure_month_partition`` / ``bi_ensure_year_partition`` are the only sanctioned
way a partition child comes into existence, because **PostgreSQL does not inherit
RLS onto partitions**: each child needs its own ENABLE, FORCE and tenant policy, and
that is precisely what these ``SECURITY DEFINER`` functions install. Their
idempotency short-circuit was::

    child := to_regclass(format('%I.%I', parent_schema, child_name));
    IF child IS NOT NULL THEN
        RETURN child;
    END IF;

which trusts the NAME. Anything resolving to ``<parent>_y2026m10`` was handed back
as the sanctioned child.

What that cost, executed as the OWNER role — which in production is the application
role, because ``risk-migrate`` runs ``alembic upgrade head`` under ``DATABASE_URL``
and so the API role owns every table:

* a hand-made ``CREATE TABLE … PARTITION OF …`` produced a child with
  ``relrowsecurity = f``, ``relforcerowsecurity = f`` and zero policies;
* a tenant-1 row inserted THROUGH THE PARENT was then readable under tenant 2's
  GUC by naming that child, and by any role holding blanket SELECT, while the same
  read through the parent correctly returned nothing;
* the ensure function returned the rogue child as if it had created it, so every
  nightly build from then on wrote into an unprotected table and every gate stayed
  green.

**And the operational trigger is real, not hypothetical.** Once rows for a month sit
in DEFAULT, the function fails with ``updated partition constraint for default
partition … would be violated by some row`` — which is exactly what sends an operator
to psql to pre-create next month's children by hand. The defect is reached by
someone doing their job.

Three checks, in the order a wrong answer would hurt
----------------------------------------------------
1. **Attached to THIS parent** (``pg_inherits``). ``to_regclass`` resolves any
   relation of that name, including a standalone table that is not a partition at
   all — which the function would have reported as covering the month while every
   row for it routed to DEFAULT. The DROP sibling already checks ``pg_inherits``;
   the ENSURE sibling did not. A mismatch RAISES rather than attaching: attaching a
   foreign table would adopt whatever rows it already holds.
2. **The bounds are the ones it would have created.** The name is not the contract.
   A correctly hardened child bounded ``TO ('2027-01-31')`` leaves the 31st routing
   to DEFAULT for good — retention drops the child and the stray rows outlive it.
   A mismatch RAISES and names both bounds.
3. **Hardened.** ENABLE + FORCE + ``<child>_tenant_isolation`` are (re)applied if any
   is missing, so a child that predates this revision is REPAIRED on the next call
   rather than refused — the fleet heals itself as the scheduler runs.

Bounds are compared as dates, not as text: ``bi_query_log`` ranges over a
``timestamptz`` and renders its bound differently from the ``date`` parents, and the
function already pins ``timezone = 'UTC'`` so the cast is stable.

This revision REPLACES the two function bodies and touches no data, so it is
reversible: ``downgrade`` restores the previous definitions verbatim. Restoring a
known-defective function is what a downgrade of this revision MEANS, and leaving the
rollback path open matters more than refusing to reinstate it — the alternative
strands the release with no way back.

The patch is applied to the definition read back from the catalogue with
``pg_get_functiondef`` and asserts on the exact text it expects to replace, so if a
later revision rewrites these functions this migration FAILS LOUDLY instead of
silently doing nothing.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "202609290079"
down_revision = "202609290078"
branch_labels = None
depends_on = None

FUNCTIONS: tuple[str, ...] = (
    "bi_ensure_month_partition",
    "bi_ensure_year_partition",
)

#: The short-circuit AND the create, as `202609220066` wrote them, whitespace-exact.
#: Both are replaced together: the verified child must fall THROUGH to the same
#: hardening the created child gets, or an adopted `bi_query_log` child would come
#: back without the append-only RESTRICTIVE policies and the trigger that the
#: creation path clones from its parent.
_OLD_SHORT_CIRCUIT = """            IF child IS NOT NULL THEN
                RETURN child;
            END IF;
            EXECUTE format(
                'CREATE TABLE %I.%I PARTITION OF %I.%I FOR VALUES FROM (%L) TO (%L)',
                parent_schema, child_name, parent_schema, parent_name, lower_bound, upper_bound
            );"""

#: The extra locals the verification needs.
_OLD_DECLARE = """            policy record;"""
_NEW_DECLARE = """            bound_text text;
            bound_lower text;
            bound_upper text;
            stale_policy text;
            policy record;"""


def _new_short_circuit(function: str) -> str:
    return f"""            IF child IS NOT NULL THEN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_catalog.pg_inherits
                     WHERE inhrelid = child AND inhparent = parent
                ) THEN
                    RAISE EXCEPTION
                        '{function}: %.% already exists and is not a partition of % '
                        '- refusing to adopt it; the period would route to DEFAULT',
                        parent_schema, child_name, parent
                        USING ERRCODE = 'wrong_object_type';
                END IF;
                SELECT pg_catalog.pg_get_expr(c.relpartbound, c.oid)
                  INTO bound_text
                  FROM pg_catalog.pg_class AS c
                 WHERE c.oid = child;
                bound_lower := (pg_catalog.regexp_match(bound_text, 'FROM \\(''([^'']+)''\\)'))[1];
                bound_upper := (pg_catalog.regexp_match(bound_text, 'TO \\(''([^'']+)''\\)'))[1];
                IF bound_lower IS NULL
                   OR bound_upper IS NULL
                   OR bound_lower::timestamptz::date <> lower_bound
                   OR bound_upper::timestamptz::date <> upper_bound THEN
                    RAISE EXCEPTION
                        '{function}: %.% is bounded % but the period needs [%, %) '
                        '- refusing to report it as covering the period',
                        parent_schema, child_name, bound_text, lower_bound, upper_bound
                        USING ERRCODE = 'wrong_object_type';
                END IF;
                -- REPAIR, rather than refuse: a child created by hand, or before this
                -- revision, becomes correct on the next call instead of waiting for an
                -- operator to find it. Every policy is dropped so the hardening below
                -- rebuilds the child EXACTLY as a freshly created one - including the
                -- parent's RESTRICTIVE append-only policies, which a tenant-isolation
                -- patch alone would have left off `bi_query_log`.
                FOR stale_policy IN
                    SELECT p.polname FROM pg_catalog.pg_policy AS p WHERE p.polrelid = child
                LOOP
                    EXECUTE format(
                        'DROP POLICY %I ON %I.%I', stale_policy, parent_schema, child_name
                    );
                END LOOP;
            ELSE
                EXECUTE format(
                    'CREATE TABLE %I.%I PARTITION OF %I.%I FOR VALUES FROM (%L) TO (%L)',
                    parent_schema, child_name, parent_schema, parent_name, lower_bound, upper_bound
                );
            END IF;"""


def _definition(function: str) -> str:
    bind = op.get_bind()
    text = bind.execute(
        sa.text(
            # Qualified to the CURRENT schema on purpose: the hermetic Postgres
            # suites migrate into a throwaway schema of their own, so an unqualified
            # name matches both that copy and any in `public`.
            "SELECT pg_catalog.pg_get_functiondef(p.oid) "
            "FROM pg_catalog.pg_proc AS p "
            "JOIN pg_catalog.pg_namespace AS n ON n.oid = p.pronamespace "
            "WHERE p.proname = :name AND n.nspname = pg_catalog.current_schema()"
        ),
        {"name": function},
    ).scalar_one()
    return str(text)


def _patch(function: str, *, verify: bool) -> str:
    body = _definition(function)
    if verify:
        if _OLD_SHORT_CIRCUIT not in body:
            message = (
                f"{function} no longer carries the short-circuit this revision "
                "replaces; a later revision has rewritten it. Re-derive the patch "
                "rather than skipping it."
            )
            raise RuntimeError(message)
        body = body.replace(_OLD_SHORT_CIRCUIT, _new_short_circuit(function), 1)
        body = body.replace(_OLD_DECLARE, _NEW_DECLARE, 1)
    else:
        body = body.replace(_new_short_circuit(function), _OLD_SHORT_CIRCUIT, 1)
        body = body.replace(_NEW_DECLARE, _OLD_DECLARE, 1)
    return body


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for function in FUNCTIONS:
        op.execute(sa.text(_patch(function, verify=True)))


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for function in FUNCTIONS:
        op.execute(sa.text(_patch(function, verify=False)))
