"""Detective controls over the REAL database's BI schema (read-only; see conftest).

Three things the migration chain gets right on the day it runs and nothing
re-checks afterwards, each named by audit A360-4:

* **Partition children carry row-level security.** Postgres copies no RLS onto a
  partition. The ``SECURITY DEFINER`` ensure functions install ENABLE + FORCE +
  the tenant policy on every child they create — but a child an operator attached
  by hand has none of it, and until finding H6 is fixed the ensure function would
  ADOPT such a child rather than repair it. The fix cannot reach a child that
  already exists in production; this invariant can. It also checks that every
  named child's bounds are the ones its name promises, so a child that stops a
  day short of its month is caught before its stray rows outlive retention.

* **The append-only tier is intact.** ``audit_events``, ``bi_query_log`` (and
  every partition of it) and ``operator_audit_log`` are guarded by a
  ``BEFORE UPDATE OR DELETE`` trigger, revoked UPDATE/DELETE/TRUNCATE and, on the
  tenant tables, FORCE RLS with RESTRICTIVE ``_no_update`` / ``_no_delete``
  policies. The table owner can undo all of it in three DDL statements
  (``DISABLE TRIGGER``, ``NO FORCE ROW LEVEL SECURITY``, ``GRANT UPDATE``) — that
  is inherent to the app role owning its tables, so DETECTION is the control.

* **The cross-tenant worker may call the partition functions.** ``202609220066``
  grants EXECUTE to the migrating role and to every BYPASSRLS login role present
  at migration time. A worker role created afterwards holds no grant, and the
  first thing it does with it — ``bi_ensure_month_partition`` before a build, or
  ``query_log.record`` on an export — fails with ``permission denied for
  function``. Documented in a migration docstring and in no runbook; asserted here
  over the roles that actually exist.

Skips, with the revision named, only when the BI foundation has not been applied
to the database under test — an invariant over partitions that do not exist would
pass while proving nothing.
"""

from __future__ import annotations

import re

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.bi import MONTHLY_PARTITIONED_TABLES, YEARLY_PARTITIONED_TABLES

BI_FOUNDATION_REVISION = "202609220066"

PARTITIONED_PARENTS: dict[str, str] = {**MONTHLY_PARTITIONED_TABLES, **YEARLY_PARTITIONED_TABLES}

#: The four ``SECURITY DEFINER`` partition functions, by signature.
PARTITION_FUNCTIONS: tuple[str, ...] = (
    "bi_ensure_month_partition(regclass, date)",
    "bi_ensure_year_partition(regclass, date)",
    "bi_drop_month_partition(regclass, date)",
    "bi_drop_year_partition(regclass, date)",
)

#: Tenant tables of the append-only tier: FORCE RLS, the trigger, the restrictive
#: policies and the revokes are all required. Partitions of ``bi_query_log`` are
#: discovered and held to the same standard.
APPEND_ONLY_TENANT_TABLES: tuple[str, ...] = ("audit_events", "bi_query_log")
#: Global (staff-plane) append-only table: trigger and revokes, no RLS by design.
APPEND_ONLY_GLOBAL_TABLES: tuple[str, ...] = ("operator_audit_log",)

_MONTH_CHILD = re.compile(r"^(?P<parent>.+)_y(?P<year>\d{4})m(?P<month>\d{2})$")
_YEAR_CHILD = re.compile(r"^(?P<parent>.+)_y(?P<year>\d{4})$")


@pytest.fixture(scope="module")
def bi_foundation(live_db: Session) -> None:
    present = live_db.execute(text("SELECT to_regclass('bi_fact_position_daily')")).scalar()
    if present is None:
        pytest.skip(
            f"the BI foundation ({BI_FOUNDATION_REVISION}) is not applied to this database; "
            "there are no BI partitions or definer functions to check"
        )
    # Partition bounds on a timestamptz key render in the session's zone; the
    # functions create them in UTC, so read them back the same way.
    live_db.execute(text("SET TIME ZONE 'UTC'"))
    live_db.commit()


def _children(db: Session, parent: str) -> list[dict[str, object]]:
    rows = db.execute(
        text(
            """
            SELECT c.relname,
                   c.relrowsecurity,
                   c.relforcerowsecurity,
                   pg_get_expr(c.relpartbound, c.oid) AS bounds,
                   (SELECT coalesce(array_agg(p.polname ORDER BY p.polname), '{}')
                      FROM pg_policy p WHERE p.polrelid = c.oid) AS policies
              FROM pg_inherits i
              JOIN pg_class c ON c.oid = i.inhrelid
             WHERE i.inhparent = to_regclass(:parent)
             ORDER BY c.relname
            """
        ),
        {"parent": parent},
    ).all()
    return [
        {
            "name": row.relname,
            "rls": bool(row.relrowsecurity),
            "force": bool(row.relforcerowsecurity),
            "bounds": row.bounds,
            "policies": set(row.policies),
        }
        for row in rows
    ]


def _expected_bounds(parent: str, child: str, *, timestamp_key: bool) -> str | None:
    """The bounds the ensure function would have created for this child name."""
    month = _MONTH_CHILD.match(child)
    year = _YEAR_CHILD.match(child)
    if month and month.group("parent") == parent:
        y, m = int(month.group("year")), int(month.group("month"))
        lower = f"{y:04d}-{m:02d}-01"
        upper = f"{y + (m == 12):04d}-{(m % 12) + 1:02d}-01"
    elif year and year.group("parent") == parent:
        y = int(year.group("year"))
        lower, upper = f"{y:04d}-01-01", f"{y + 1:04d}-01-01"
    else:
        return None
    if timestamp_key:
        return f"FOR VALUES FROM ('{lower} 00:00:00+00') TO ('{upper} 00:00:00+00')"
    return f"FOR VALUES FROM ('{lower}') TO ('{upper}')"


@pytest.mark.usefixtures("bi_foundation")
def test_every_partition_child_forces_rls_under_its_own_tenant_policy(live_db: Session) -> None:
    """A child without ENABLE + FORCE + ``<child>_tenant_isolation`` is a table
    every tenant can read by naming it, whatever the parent's policies say."""
    unprotected: list[str] = []
    seen = 0
    for parent in PARTITIONED_PARENTS:
        for child in _children(live_db, parent):
            seen += 1
            name = str(child["name"])
            if not (
                child["rls"] and child["force"] and f"{name}_tenant_isolation" in child["policies"]  # type: ignore[operator]
            ):
                unprotected.append(
                    f"{name}: relrowsecurity={child['rls']}, relforcerowsecurity="
                    f"{child['force']}, policies={sorted(child['policies'])}"  # type: ignore[arg-type]
                )
    assert seen >= len(PARTITIONED_PARENTS), (
        f"only {seen} partitions found under {len(PARTITIONED_PARENTS)} parents; every parent "
        "has at least its DEFAULT partition, so the catalogue read is wrong"
    )
    assert not unprotected, (
        "these BI partitions were not created (or not repaired) by the definer functions "
        "and can be read cross-tenant by name — a hand-made child. Harden each with ENABLE "
        "+ FORCE ROW LEVEL SECURITY and the tenant policy, then find who created it:\n  "
        + "\n  ".join(unprotected)
    )


@pytest.mark.usefixtures("bi_foundation")
def test_every_named_partition_child_carries_the_bounds_its_name_promises(
    live_db: Session,
) -> None:
    """``<parent>_y2026m10`` must be exactly [2026-10-01, 2026-11-01). A child that
    is attached under the right name with the wrong range routes the days it
    misses to DEFAULT for good, and retention then drops the child and keeps the
    strays. Any other child name is not one the functions derive — a hand-made
    partition by definition."""
    wrong: list[str] = []
    for parent, key_column in PARTITIONED_PARENTS.items():
        timestamp_key = bool(
            live_db.execute(
                text(
                    "SELECT format_type(a.atttypid, a.atttypmod) LIKE 'timestamp%' "
                    "FROM pg_attribute a WHERE a.attrelid = to_regclass(:parent) "
                    "AND a.attname = :column"
                ),
                {"parent": parent, "column": key_column},
            ).scalar()
        )
        for child in _children(live_db, parent):
            name = str(child["name"])
            if name == f"{parent}_default":
                if child["bounds"] != "DEFAULT":
                    wrong.append(f"{name}: expected DEFAULT, found {child['bounds']}")
                continue
            expected = _expected_bounds(parent, name, timestamp_key=timestamp_key)
            if expected is None:
                wrong.append(f"{name}: not a name the partition functions derive")
            elif child["bounds"] != expected:
                wrong.append(f"{name}: expected {expected}, found {child['bounds']}")
    assert not wrong, "partition bounds disagree with the partition names:\n  " + "\n  ".join(wrong)


@pytest.mark.usefixtures("bi_foundation")
def test_every_partitioned_parent_forces_rls_under_its_tenant_policy(live_db: Session) -> None:
    state = {}
    for parent in PARTITIONED_PARENTS:
        row = live_db.execute(
            text(
                """
                SELECT c.relname, c.relkind, c.relrowsecurity, c.relforcerowsecurity,
                       EXISTS (SELECT 1 FROM pg_policy p
                                WHERE p.polrelid = c.oid
                                  AND p.polname = c.relname || '_tenant_isolation') AS policy
                  FROM pg_class c
                 WHERE c.oid = to_regclass(:parent)
                """
            ),
            {"parent": parent},
        ).one_or_none()
        if row is not None:
            state[str(row.relname)] = row
    assert set(state) == set(PARTITIONED_PARENTS), sorted(set(PARTITIONED_PARENTS) ^ set(state))
    bad = [
        name
        for name, row in state.items()
        if not (
            row.relkind == "p" and row.relrowsecurity and row.relforcerowsecurity and row.policy
        )
    ]
    assert not bad, f"partitioned parents not FORCE-RLS under the tenant policy: {bad}"


# --- the append-only tier -------------------------------------------------------


def _append_only_relations(db: Session) -> list[tuple[str, str, bool]]:
    """``(relation, guard trigger name, is tenant table)`` for the whole tier,
    partitions included — a child inherits the PARENT's trigger name."""
    relations: list[tuple[str, str, bool]] = []
    for table in APPEND_ONLY_TENANT_TABLES:
        relations.append((table, f"{table}_append_only", True))
        for child in db.execute(
            text(
                "SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
                "WHERE i.inhparent = to_regclass(:parent) ORDER BY c.relname"
            ),
            {"parent": table},
        ).scalars():
            relations.append((str(child), f"{table}_append_only", True))
    relations.extend((table, f"{table}_append_only", False) for table in APPEND_ONLY_GLOBAL_TABLES)
    return relations


def test_the_append_only_tier_is_present(live_db: Session) -> None:
    """The tier's own existence, so the tests below cannot pass over nothing."""
    missing = [
        table
        for table in (*APPEND_ONLY_TENANT_TABLES, *APPEND_ONLY_GLOBAL_TABLES)
        if live_db.execute(text("SELECT to_regclass(:t)"), {"t": table}).scalar() is None
    ]
    assert not missing, f"append-only tables absent from this database: {missing}"


def test_every_append_only_guard_trigger_is_present_and_enabled(live_db: Session) -> None:
    """``tgenabled`` must be ``O`` (origin) or ``A`` (always); ``D`` is the
    one-statement defeat, ``R`` disables it on the primary."""
    broken: list[str] = []
    for relation, trigger, _tenant in _append_only_relations(live_db):
        state = live_db.execute(
            text(
                "SELECT t.tgenabled::text FROM pg_trigger t "
                "WHERE t.tgrelid = to_regclass(:relation) AND t.tgname = :trigger "
                "AND NOT t.tgisinternal"
            ),
            {"relation": relation, "trigger": trigger},
        ).scalar()
        if state is None:
            broken.append(f"{relation}: trigger {trigger} is MISSING")
        elif state not in ("O", "A"):
            broken.append(f"{relation}: trigger {trigger} has tgenabled={state!r} (disabled)")
    assert not broken, (
        "the append-only row trigger has been removed or disabled:\n  " + "\n  ".join(broken)
    )


def test_every_append_only_tenant_relation_forces_rls_with_the_restrictive_policies(
    live_db: Session,
) -> None:
    """``NO FORCE ROW LEVEL SECURITY`` is the second one-statement defeat: without
    FORCE the owner is exempt from the ``_no_update`` / ``_no_delete`` policies."""
    broken: list[str] = []
    for relation, _trigger, tenant in _append_only_relations(live_db):
        if not tenant:
            continue
        row = live_db.execute(
            text(
                """
                SELECT c.relrowsecurity, c.relforcerowsecurity,
                       (SELECT coalesce(array_agg(p.polname ORDER BY p.polname), '{}')
                          FROM pg_policy p WHERE p.polrelid = c.oid) AS policies
                  FROM pg_class c WHERE c.oid = to_regclass(:relation)
                """
            ),
            {"relation": relation},
        ).one()
        policies = set(row.policies)
        required = {
            f"{relation}_no_update",
            f"{relation}_no_delete",
            f"{relation}_tenant_isolation",
        }
        if not (row.relrowsecurity and row.relforcerowsecurity):
            broken.append(
                f"{relation}: relrowsecurity={row.relrowsecurity}, "
                f"relforcerowsecurity={row.relforcerowsecurity}"
            )
        if not required <= policies:
            broken.append(f"{relation}: missing policies {sorted(required - policies)}")
    assert not broken, "the append-only tier's RLS has been weakened:\n  " + "\n  ".join(broken)


def test_no_login_role_holds_update_delete_or_truncate_on_the_append_only_tier(
    live_db: Session,
) -> None:
    """``GRANT UPDATE`` is the third defeat. The migrations revoked these from
    every role present at the time; a role created later starts with nothing, so
    ANY holder today was granted deliberately. Superusers are exempt by nature
    and are listed, not asserted — the platform must not connect as one."""
    roles = [
        str(name)
        for name in live_db.execute(
            text(
                "SELECT rolname FROM pg_roles WHERE rolcanlogin AND NOT rolsuper "
                "AND rolname NOT LIKE 'pg\\_%' ORDER BY rolname"
            )
        ).scalars()
    ]
    assert roles, "no non-superuser login role exists; the platform cannot be connecting as one"
    holders: list[str] = []
    for relation, _trigger, _tenant in _append_only_relations(live_db):
        for role in roles:
            held = [
                privilege
                for privilege in ("UPDATE", "DELETE", "TRUNCATE")
                if live_db.execute(
                    text("SELECT has_table_privilege(:role, to_regclass(:relation), :privilege)"),
                    {"role": role, "relation": relation, "privilege": privilege},
                ).scalar()
            ]
            if held:
                holders.append(f"{relation}: {role} holds {held}")
    assert not holders, (
        "a login role can rewrite or truncate an append-only table (TRUNCATE bypasses "
        "row triggers entirely):\n  " + "\n  ".join(holders)
    )


# --- EXECUTE on the definer functions --------------------------------------------


@pytest.mark.usefixtures("bi_foundation")
def test_every_bypassrls_login_role_and_this_session_may_call_the_partition_functions(
    live_db: Session,
) -> None:
    """The deployment landmine, asserted over the roles that exist.

    The cross-tenant worker (``WORKER_DATABASE_URL``) is BYPASSRLS by requirement
    and is what calls ``bi_ensure_month_partition`` before every build and
    ``query_log.record`` on every export and delivery. A worker role created
    after ``202609220066`` ran has no EXECUTE and fails on first use. The session
    running this suite is expected to BE that worker, so it is asserted too.
    """
    workers = [
        str(name)
        for name in live_db.execute(
            text(
                "SELECT rolname FROM pg_roles WHERE rolbypassrls AND rolcanlogin AND NOT rolsuper "
                "AND rolname NOT LIKE 'pg\\_%' ORDER BY rolname"
            )
        ).scalars()
    ]
    current = str(live_db.execute(text("SELECT current_user")).scalar())
    callers = sorted({*workers, current})
    denied: list[str] = []
    for signature in PARTITION_FUNCTIONS:
        exists = live_db.execute(
            text("SELECT to_regprocedure(:signature)"), {"signature": signature}
        ).scalar()
        if exists is None:
            denied.append(f"{signature}: function is MISSING")
            continue
        for role in callers:
            granted = live_db.execute(
                text("SELECT has_function_privilege(:role, to_regprocedure(:sig), 'EXECUTE')"),
                {"role": role, "sig": signature},
            ).scalar()
            if not granted:
                denied.append(f"{signature}: {role} may not EXECUTE")
    assert not denied, (
        "a role the BI plane runs as cannot call the partition functions — every build "
        "that needs a new month, every export and every subscription delivery under it "
        "fails with `permission denied for function`. Fix: GRANT EXECUTE ON FUNCTION "
        "<each signature> TO <role>, as the migration docstring prescribes for roles "
        "created after it ran:\n  " + "\n  ".join(denied)
    )


@pytest.mark.usefixtures("bi_foundation")
def test_public_may_not_call_the_partition_functions(live_db: Session) -> None:
    """The grant must be explicit per role; a PUBLIC grant would make the definer
    functions a ``CREATE TABLE`` gadget for every tenant connection."""
    open_to_public = [
        signature
        for signature in PARTITION_FUNCTIONS
        if live_db.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM pg_proc p, aclexplode(p.proacl) a "
                "WHERE p.oid = to_regprocedure(:sig) AND a.grantee = 0 "
                "AND a.privilege_type = 'EXECUTE')"
            ),
            {"sig": signature},
        ).scalar()
    ]
    assert not open_to_public, f"EXECUTE is granted to PUBLIC on: {open_to_public}"
