"""The ensure-partition functions must not ADOPT a child they did not harden.

Audit A360-4 finding H6. ``bi_ensure_month_partition`` / ``bi_ensure_year_partition``
(migration ``202609220066``) exist for exactly one reason: Postgres does not copy
row-level security onto a partition, so a child created by anything other than
these functions is a table every tenant can read by naming it. The functions'
idempotency short-circuit, however, trusted the NAME — ``to_regclass`` on the
derived child name, ``IF child IS NOT NULL THEN RETURN child`` — so a child that
already existed was returned as if the function had created it, whatever its
state:

* a partition an operator attached by hand (``relrowsecurity = f``,
  ``relforcerowsecurity = f``, no policy) was returned unrepaired, and a tenant's
  rows written through the parent were then readable by naming the child under
  another tenant's GUC and under no GUC at all, while every read through the
  parent stayed correctly at zero;
* ``to_regclass`` does not consult ``pg_inherits``, so an UNATTACHED table that
  happened to carry the derived name was adopted too — the DROP sibling checks
  ``pg_inherits`` and the ENSURE sibling did not.

The existing guard (``test_ensure_partition_functions_are_idempotent_and_install_rls``)
only ever inspected children the function created ITSELF, which is why it stayed
green. The operational trigger is real and is pinned here as well: once rows for a
month sit in the DEFAULT partition, creating that month's child fails on the
default-partition constraint, and that failure is precisely what pushes an operator
to pre-create next month's children by hand.

Every test here runs as the role that ran the migration and owns the tables — the
production app role — because FORCE is what makes RLS bind the owner. Postgres-
gated on ``TEST_DATABASE_URL``; a role that bypasses RLS proves nothing, so the
module FAILS rather than skips under ``POSTGRES_PRIVILEGE_TESTS_REQUIRED``.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError

from alembic import command

# Imported for its side effect: app.db.session registers the ``after_begin``
# listener that sets the tenant GUC.
from app.db.session import set_tenant_rls_context
from tests.api.helpers import ORG_1, ORG_2

# The two privilege helpers are shared deliberately: "skip locally, FAIL in the
# gate" is one policy and must not be re-stated per module.
from tests.db.test_bi_foundation_migration import (
    _require_or_skip_privileges,
    _temporary_non_owner_role,
)
from tests.db.test_postgres_migrations import (
    MigratedPostgresSchema,
    alembic_config_for_app,
    clear_database_caches,
    postgres_schema_url,
)

_ = set_tenant_rls_context

pytestmark = pytest.mark.skipif(
    os.getenv("TEST_DATABASE_URL") is None,
    reason="TEST_DATABASE_URL is required for Postgres RLS tests.",
)

BANK_A = "BK-ADOPTA001"
BANK_B = "BK-ADOPTB002"

#: The four ``SECURITY DEFINER`` functions, by signature.
PARTITION_FUNCTIONS: tuple[str, ...] = (
    "bi_ensure_month_partition(regclass, date)",
    "bi_ensure_year_partition(regclass, date)",
    "bi_drop_month_partition(regclass, date)",
    "bi_drop_year_partition(regclass, date)",
)


# --- the cases -----------------------------------------------------------------
#
# Each case names a parent, the ensure function for its cadence, the child the
# function derives for ``probe`` and the bounds a correctly created child carries.
# Months and years are disjoint across cases so one module-scoped schema serves
# them all.


class _Case:
    def __init__(  # noqa: PLR0913 - a fixture record
        self,
        *,
        parent: str,
        function: str,
        probe: date,
        child: str,
        lower: str,
        upper: str,
        insert: Callable[[Connection, str, str, date], None],
    ) -> None:
        self.parent = parent
        self.function = function
        self.probe = probe
        self.child = child
        self.lower = lower
        self.upper = upper
        self.insert = insert

    @property
    def id(self) -> str:
        return f"{self.parent}:{self.child}"


def _insert_position(connection: Connection, parent: str, org: str, bank: str, day: date) -> None:
    connection.execute(
        text(
            f"""
            INSERT INTO {parent}
              (as_of_date, snapshot_id, position_id, organization_id, bank_id, source_system,
               source_reference, position_type, currency, balance_native, balance_rc,
               fx_unconverted, builder_version, built_at)
            VALUES
              (:as_of, :snapshot_id, :position_id, :org, :bank, 'API_PUSH', :reference,
               'LOAN', 'GHS', :balance, :balance, false, 1, now())
            """
        ),
        {
            "as_of": day,
            "snapshot_id": str(uuid4()),
            "position_id": str(uuid4()),
            "org": org,
            "bank": bank,
            "reference": f"{bank}:{parent}:{day.isoformat()}",
            "balance": Decimal("1000.000000"),
        },
    )


def _insert_daily(connection: Connection, org: str, bank: str, day: date) -> None:
    _insert_position(connection, "bi_fact_position_daily", org, bank, day)


def _insert_eom(connection: Connection, org: str, bank: str, day: date) -> None:
    _insert_position(connection, "bi_fact_position_eom", org, bank, day)


def _insert_query_log(connection: Connection, org: str, bank: str, day: date) -> None:
    connection.execute(
        text(
            """
            INSERT INTO bi_query_log
              (queried_at, id, organization_id, bank_id, principal_user_id, principal_type,
               surface, query_hash, member_ids, decision, denied_members, catalogue_version)
            VALUES
              (:queried_at, :id, :org, :bank, :principal, 'human', 'query', :query_hash,
               '["m.one"]', 'allowed', '[]', 'v1')
            """
        ),
        {
            "queried_at": datetime(day.year, day.month, day.day, 9, tzinfo=UTC),
            "id": str(uuid4()),
            "org": org,
            "bank": bank,
            "principal": str(uuid4()),
            "query_hash": "c" * 64,
        },
    )


#: An attached, UNPROTECTED child per cadence — plus the query log, whose children
#: must additionally carry the append-only denial the create path installs.
ADOPTION_CASES: tuple[_Case, ...] = (
    _Case(
        parent="bi_fact_position_daily",
        function="bi_ensure_month_partition",
        probe=date(2026, 10, 15),
        child="bi_fact_position_daily_y2026m10",
        lower="2026-10-01",
        upper="2026-11-01",
        insert=_insert_daily,
    ),
    _Case(
        parent="bi_fact_position_eom",
        function="bi_ensure_year_partition",
        probe=date(2028, 6, 30),
        child="bi_fact_position_eom_y2028",
        lower="2028-01-01",
        upper="2029-01-01",
        insert=_insert_eom,
    ),
    _Case(
        parent="bi_query_log",
        function="bi_ensure_month_partition",
        probe=date(2026, 10, 15),
        child="bi_query_log_y2026m10",
        lower="2026-10-01 00:00:00+00",
        upper="2026-11-01 00:00:00+00",
        insert=_insert_query_log,
    ),
)

#: A standalone table wearing the derived name, attached to nothing.
UNATTACHED_CASES: tuple[_Case, ...] = (
    _Case(
        parent="bi_fact_position_daily",
        function="bi_ensure_month_partition",
        probe=date(2026, 11, 3),
        child="bi_fact_position_daily_y2026m11",
        lower="2026-11-01",
        upper="2026-12-01",
        insert=_insert_daily,
    ),
    _Case(
        parent="bi_fact_position_eom",
        function="bi_ensure_year_partition",
        probe=date(2029, 3, 31),
        child="bi_fact_position_eom_y2029",
        lower="2029-01-01",
        upper="2030-01-01",
        insert=_insert_eom,
    ),
)

#: A month with no child, whose rows will therefore sit in DEFAULT.
DEFAULT_CONFLICT_DAY = date(2026, 12, 15)
DEFAULT_CONFLICT_CHILD = "bi_fact_position_daily_y2026m12"

#: A hand-created child that IS hardened but carries the WRONG bounds: it stops a
#: day short of the month, so the 31st routes to DEFAULT forever.
SHORT_BOUNDS_CHILD = "bi_fact_position_daily_y2027m01"
SHORT_BOUNDS_PROBE = date(2027, 1, 10)


# --- helpers -------------------------------------------------------------------


def _set_tenant(connection: Connection, organization_id: str | None) -> None:
    connection.execute(
        text("SELECT set_config('app.organization_id', :organization_id, true)"),
        {"organization_id": "" if organization_id is None else organization_id},
    )


def _seed_tenant(connection: Connection, *, organization_id: str, bank_id: str) -> None:
    with connection.begin():
        _set_tenant(connection, organization_id)
        connection.execute(
            text(
                "INSERT INTO organizations (id, name, created_at, updated_at) "
                "VALUES (:organization_id, :name, now(), now())"
            ),
            {"organization_id": organization_id, "name": f"Tenant {organization_id}"},
        )
        connection.execute(
            text(
                """
                INSERT INTO banks
                  (id, organization_id, name, short_name, currency, jurisdiction_code,
                   license_type, institution_type, created_at, updated_at)
                VALUES
                  (:bank_id, :organization_id, :name, :short_name, 'GHS', 'GH',
                   'universal', 'universal_bank', now(), now())
                """
            ),
            {
                "bank_id": bank_id,
                "organization_id": organization_id,
                "name": f"{organization_id} Bank",
                "short_name": bank_id,
            },
        )


def _relation_state(connection: Connection, schema_name: str, relation: str) -> dict[str, object]:
    """What a hardened child looks like, in one row — ``None`` when absent."""
    row = connection.execute(
        text(
            """
            SELECT c.relkind, c.relrowsecurity, c.relforcerowsecurity,
                   EXISTS (SELECT 1 FROM pg_inherits i WHERE i.inhrelid = c.oid) AS attached,
                   (SELECT coalesce(array_agg(p.policyname ORDER BY p.policyname), '{}')
                      FROM pg_policies p
                     WHERE p.schemaname = n.nspname AND p.tablename = c.relname) AS policies,
                   pg_get_expr(c.relpartbound, c.oid) AS bounds
              FROM pg_class c
              JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = :schema_name AND c.relname = :relation
            """
        ),
        {"schema_name": schema_name, "relation": relation},
    ).one_or_none()
    if row is None:
        return {}
    return {
        "relkind": row.relkind,
        "rls": bool(row.relrowsecurity),
        "force": bool(row.relforcerowsecurity),
        "attached": bool(row.attached),
        "policies": set(row.policies),
        "bounds": row.bounds,
    }


def _is_hardened(state: dict[str, object], child: str) -> bool:
    return bool(
        state
        and state["rls"]
        and state["force"]
        and f"{child}_tenant_isolation" in state["policies"]  # type: ignore[operator]
    )


def _call_ensure(engine, case: _Case) -> tuple[str, str]:  # noqa: ANN001 - a SQLAlchemy Engine
    """``("returned", <child name>)`` or ``("raised", <message>)`` — never both."""
    try:
        with engine.begin() as connection:
            value = connection.scalar(
                text(f"SELECT {case.function}(:parent, :probe)"),
                {"parent": case.parent, "probe": case.probe},
            )
    except DBAPIError as exc:
        return "raised", str(exc.orig)
    return "returned", str(value)


def _count_as(engine, relation: str, organization_id: str | None) -> int:  # noqa: ANN001
    with engine.connect() as connection, connection.begin():
        _set_tenant(connection, organization_id)
        return int(connection.scalar(text(f"SELECT count(*) FROM {relation}")))


# --- fixture -------------------------------------------------------------------


@pytest.fixture(scope="module")
def adoption_schema() -> Iterator[MigratedPostgresSchema]:
    """A migrated schema with two tenants and NO runtime children yet.

    Each test plants its own pre-existing relation, so the function meets the
    exact situation the audit reproduced: a same-named relation it did not make.
    """
    test_database_url = os.environ["TEST_DATABASE_URL"]
    schema_name = f"risk_service_bi_adopt_{uuid4().hex}"
    database_url = postgres_schema_url(test_database_url, schema_name)
    monkeypatch = pytest.MonkeyPatch()
    admin_engine = create_engine(test_database_url, isolation_level="AUTOCOMMIT")
    app_engine = create_engine(database_url)
    monkeypatch.setenv("DATABASE_URL", database_url)
    clear_database_caches()

    with admin_engine.connect() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))

    try:
        command.upgrade(alembic_config_for_app(), "head")
        with app_engine.connect() as connection:
            role = connection.execute(
                text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
            ).one()
            connection.rollback()
        if role[0] or role[1]:
            _require_or_skip_privileges(
                "the TEST_DATABASE_URL role bypasses RLS, so an unprotected child cannot be "
                "told from a hardened one"
            )
        with app_engine.connect() as connection:
            _seed_tenant(connection, organization_id=ORG_1, bank_id=BANK_A)
            _seed_tenant(connection, organization_id=ORG_2, bank_id=BANK_B)
        yield MigratedPostgresSchema(app_engine=app_engine, schema_name=schema_name)
    finally:
        monkeypatch.undo()
        clear_database_caches()
        app_engine.dispose()
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        admin_engine.dispose()


def _plant_unprotected_child(schema: MigratedPostgresSchema, case: _Case) -> dict[str, object]:
    """``CREATE TABLE … PARTITION OF …`` by hand, exactly as an operator would."""
    with schema.app_engine.begin() as connection:
        connection.execute(
            text(
                f"CREATE TABLE {case.child} PARTITION OF {case.parent} "
                f"FOR VALUES FROM ('{case.lower}') TO ('{case.upper}')"
            )
        )
    with schema.app_engine.connect() as connection:
        state = _relation_state(connection, schema.schema_name, case.child)
    # The premise, asserted: Postgres copied NOTHING. If this ever starts failing,
    # Postgres has changed and this whole module needs re-reading.
    assert state["attached"] is True and state["relkind"] == "r", state
    assert state["rls"] is False and state["force"] is False, state
    assert not {p for p in state["policies"] if p.startswith(case.child)}, state  # type: ignore[union-attr]
    return state


# --- (a) an attached, unprotected child ----------------------------------------


@pytest.mark.parametrize("case", ADOPTION_CASES, ids=lambda case: case.id)
def test_a_pre_existing_unprotected_child_is_repaired_or_refused_never_adopted(
    adoption_schema: MigratedPostgresSchema, case: _Case
) -> None:
    """The short-circuit may return a child ONLY when the child is hardened.

    Two outcomes are acceptable: the function repairs the child (ENABLE + FORCE +
    the tenant policy, and for the query log the append-only denial too) and
    returns it, or it refuses with an error naming the relation. What it must
    never do is what it did: return the rogue child as if it had created it.
    """
    _plant_unprotected_child(adoption_schema, case)

    outcome, value = _call_ensure(adoption_schema.app_engine, case)

    with adoption_schema.app_engine.connect() as connection:
        after = _relation_state(connection, adoption_schema.schema_name, case.child)

    if outcome == "raised":
        assert case.child in value or case.parent in value, (
            f"{case.function} refused without naming the relation: {value}"
        )
        return

    assert value == case.child
    assert _is_hardened(after, case.child), (
        f"{case.function} returned the pre-existing {case.child} WITHOUT hardening it: "
        f"relrowsecurity={after.get('rls')}, relforcerowsecurity={after.get('force')}, "
        f"policies={sorted(after.get('policies', ()))}. The function trusted the name; "  # type: ignore[arg-type]
        "a child it did not create is a child every tenant can read by naming it."
    )
    if case.parent == "bi_query_log":
        # The create path mirrors the parent's RESTRICTIVE policies and revokes
        # UPDATE/DELETE/TRUNCATE; an adopted child must end up the same.
        assert {f"{case.child}_no_update", f"{case.child}_no_delete"} <= after["policies"], (  # type: ignore[operator]
            f"adopted {case.child} lacks the append-only policies: {sorted(after['policies'])}"  # type: ignore[arg-type]
        )
        with adoption_schema.app_engine.connect() as connection:
            held = {
                privilege: connection.scalar(
                    text("SELECT has_table_privilege(current_user, :child, :privilege)"),
                    {
                        "child": f"{adoption_schema.schema_name}.{case.child}",
                        "privilege": privilege,
                    },
                )
                for privilege in ("UPDATE", "DELETE", "TRUNCATE")
            }
        assert not any(held.values()), f"the owner still holds {held} on adopted {case.child}"


# --- (b) a cross-tenant read through that child --------------------------------


@pytest.mark.parametrize("case", ADOPTION_CASES, ids=lambda case: case.id)
def test_a_tenant_row_in_an_adopted_child_is_invisible_to_every_other_reader(
    adoption_schema: MigratedPostgresSchema, case: _Case
) -> None:
    """After the ensure function has run, naming the child must leak nothing.

    Tenant A writes one row THROUGH THE PARENT (the only way the product writes);
    it routes into the pre-existing child. Tenant B, a session with no tenant at
    all, and a non-owner role holding a blanket SELECT then each read the child
    BY NAME. Every count must be zero. The parent path already returns zero
    (``test_bi_partition_rls.py``) — that is exactly why this hole was invisible.

    The function is run BEFORE the row is planted, so a design that refuses
    rather than repairs is measured on its consequence: a refusal that leaves
    the unprotected child attached leaves the leak in place, and this test says
    so rather than treating the error as a fix.
    """
    with adoption_schema.app_engine.connect() as connection:
        # The (a) case for this parametrisation may already have planted it.
        present = _relation_state(connection, adoption_schema.schema_name, case.child)
    if not present:
        _plant_unprotected_child(adoption_schema, case)

    outcome, value = _call_ensure(adoption_schema.app_engine, case)

    with adoption_schema.app_engine.connect() as connection, connection.begin():
        _set_tenant(connection, ORG_1)
        case.insert(connection, ORG_1, BANK_A, case.probe)
        routed = connection.scalar(
            text(
                f"SELECT tableoid::regclass::text FROM {case.parent} "
                "WHERE organization_id = :org AND bank_id = :bank"
            ),
            {"org": ORG_1, "bank": BANK_A},
        )
    assert routed == case.child, f"the planted row did not route into {case.child}: {routed}"

    own = _count_as(adoption_schema.app_engine, case.child, ORG_1)
    other_tenant = _count_as(adoption_schema.app_engine, case.child, ORG_2)
    no_tenant = _count_as(adoption_schema.app_engine, case.child, None)

    assert own == 1, f"tenant A cannot see its own row in {case.child} ({own})"
    leaks = {
        label: seen
        for label, seen in (("another tenant's GUC", other_tenant), ("no tenant GUC", no_tenant))
        if seen != 0
    }
    assert not leaks, (
        f"{case.child} serves tenant A's row to {leaks} when addressed by name, after "
        f"{case.function} {outcome} ({value}). The parent path returns zero for the same "
        "reads, which is why this went unseen. "
        + (
            "The function refused instead of repairing, and a refusal does not close the "
            "child it refused."
            if outcome == "raised"
            else "The function returned the child as its own without hardening it."
        )
    )

    # A non-owner with a blanket SELECT sees the same zero: ENABLE binds non-owners
    # even where FORCE is what binds the owner.
    role = f"bi_reader_{uuid4().hex[:12]}"
    schema_name = adoption_schema.schema_name
    with adoption_schema.app_engine.connect() as admin:
        if not _temporary_non_owner_role(admin, role):
            _require_or_skip_privileges(
                "the TEST_DATABASE_URL role cannot create a second role (CREATEROLE), so a "
                "non-owner reader cannot be proven"
            )
    try:
        with adoption_schema.app_engine.begin() as admin:
            admin.execute(text(f'GRANT USAGE ON SCHEMA "{schema_name}" TO {role}'))
            admin.execute(text(f"GRANT SELECT ON {case.child} TO {role}"))
        with adoption_schema.app_engine.connect() as connection, connection.begin():
            connection.execute(text(f"SET LOCAL ROLE {role}"))
            seen_by_reader = int(connection.scalar(text(f"SELECT count(*) FROM {case.child}")))
        assert seen_by_reader == 0, (
            f"a non-owner role with SELECT on {case.child} reads tenant A's row "
            f"({seen_by_reader}) — row-level security is not even ENABLED on the child"
        )
    finally:
        with adoption_schema.app_engine.begin() as admin:
            admin.execute(text(f"REVOKE ALL ON {case.child} FROM {role}"))
            admin.execute(text(f'REVOKE ALL ON SCHEMA "{schema_name}" FROM {role}'))
            admin.execute(text(f"DROP ROLE {role}"))


# --- (c) an unattached table wearing the derived name --------------------------


@pytest.mark.parametrize("case", UNATTACHED_CASES, ids=lambda case: case.id)
def test_an_unattached_table_with_the_derived_name_is_refused_not_adopted(
    adoption_schema: MigratedPostgresSchema, case: _Case
) -> None:
    """``to_regclass`` resolves any relation of that name; only ``pg_inherits``
    says whether it is a partition of THIS parent. The drop sibling checks it and
    refuses "is not a partition of"; the ensure sibling must do the same rather
    than hand back a table whose rows the parent will never see and whose
    absence as a partition sends the month's rows to DEFAULT."""
    with adoption_schema.app_engine.begin() as connection:
        connection.execute(text(f"CREATE TABLE {case.child} (LIKE {case.parent} INCLUDING ALL)"))
    with adoption_schema.app_engine.connect() as connection:
        before = _relation_state(connection, adoption_schema.schema_name, case.child)
    assert before["attached"] is False and before["bounds"] is None, before

    outcome, value = _call_ensure(adoption_schema.app_engine, case)

    with adoption_schema.app_engine.connect() as connection:
        after = _relation_state(connection, adoption_schema.schema_name, case.child)
        partitions = set(
            connection.execute(
                text(
                    "SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
                    "WHERE i.inhparent = to_regclass(:parent)"
                ),
                {"parent": f"{adoption_schema.schema_name}.{case.parent}"},
            ).scalars()
        )

    assert outcome == "raised", (
        f"{case.function} returned {value!r} for a standalone table that is NOT a partition "
        f"of {case.parent}. Rows for {case.probe:%Y-%m} will route to DEFAULT while the "
        "function reports the month covered."
    )
    assert case.child in value, f"the refusal does not name the relation: {value}"
    # Refused, not repaired-by-attaching: attaching a foreign table adopts its rows.
    assert after["attached"] is False, f"{case.child} was ATTACHED to {case.parent}"
    assert case.child not in partitions


# --- (d) the operational trigger: rows already in DEFAULT ----------------------


def test_creating_a_child_for_a_month_whose_rows_sit_in_default_fails_on_the_constraint(
    adoption_schema: MigratedPostgresSchema,
) -> None:
    """Pinned because it is the reason an operator reaches for hand-made children.

    A row for a month with no child routes to ``<parent>_default``. The ensure
    function for that month then fails — Postgres will not tighten the DEFAULT
    partition's constraint while a row violates it. The builder ensures partitions
    BEFORE its first row precisely so this never happens in the product's own
    path; when it happens anyway (a backfill that outran its ensure, a manual
    load), nothing in the platform moves the rows, and the operator's cheapest
    fix is ``CREATE TABLE … PARTITION OF`` by hand — the very act (a) and (b)
    exist for. Whoever makes this path succeed (moving the rows, or refusing with
    an operator instruction) must revisit those two tests: they assume the
    hand-made child is the operator's escape hatch.
    """
    engine = adoption_schema.app_engine
    with engine.connect() as connection, connection.begin():
        _set_tenant(connection, ORG_1)
        _insert_daily(connection, ORG_1, BANK_A, DEFAULT_CONFLICT_DAY)
        routed = connection.scalar(
            text(
                "SELECT tableoid::regclass::text FROM bi_fact_position_daily "
                "WHERE as_of_date = :day"
            ),
            {"day": DEFAULT_CONFLICT_DAY},
        )
    assert routed == "bi_fact_position_daily_default", routed

    case = _Case(
        parent="bi_fact_position_daily",
        function="bi_ensure_month_partition",
        probe=DEFAULT_CONFLICT_DAY,
        child=DEFAULT_CONFLICT_CHILD,
        lower="2026-12-01",
        upper="2027-01-01",
        insert=_insert_daily,
    )
    outcome, value = _call_ensure(engine, case)

    assert outcome == "raised", (
        f"creating {DEFAULT_CONFLICT_CHILD} succeeded with a row for that month already in "
        "DEFAULT; the operational trigger this module documents no longer exists — re-read "
        "tests (a) and (b)"
    )
    assert "default partition" in value and "violated" in value, value
    with engine.connect() as connection:
        assert not _relation_state(connection, adoption_schema.schema_name, DEFAULT_CONFLICT_CHILD)
        connection.rollback()
        with connection.begin():
            _set_tenant(connection, ORG_1)
            still_in_default = connection.scalar(
                text("SELECT count(*) FROM bi_fact_position_daily_default WHERE as_of_date = :day"),
                {"day": DEFAULT_CONFLICT_DAY},
            )
    assert still_in_default == 1


# --- (e) a hardened child with the wrong bounds ---------------------------------


def test_a_hardened_child_whose_bounds_are_not_the_months_is_not_reported_as_covering_it(
    adoption_schema: MigratedPostgresSchema,
) -> None:
    """Beyond the RLS check: the name is not the contract, the BOUNDS are.

    An operator who hardens a hand-made child correctly but writes ``TO
    ('2027-01-31')`` leaves the 31st routing to DEFAULT for good — retention drops
    the child and the stray rows survive it, and every query for that day scans
    DEFAULT. A short-circuit that checks RLS alone would return this child as
    the month's. The function must compare ``relpartbound`` with the bounds it
    would have created and refuse on a mismatch, naming both.
    """
    engine = adoption_schema.app_engine
    schema_name = adoption_schema.schema_name
    with engine.begin() as connection:
        connection.execute(
            text(
                f"CREATE TABLE {SHORT_BOUNDS_CHILD} PARTITION OF bi_fact_position_daily "
                "FOR VALUES FROM ('2027-01-01') TO ('2027-01-31')"
            )
        )
        connection.execute(text(f"ALTER TABLE {SHORT_BOUNDS_CHILD} ENABLE ROW LEVEL SECURITY"))
        connection.execute(text(f"ALTER TABLE {SHORT_BOUNDS_CHILD} FORCE ROW LEVEL SECURITY"))
        connection.execute(
            text(
                f"CREATE POLICY {SHORT_BOUNDS_CHILD}_tenant_isolation ON {SHORT_BOUNDS_CHILD} "
                "FOR ALL USING ((organization_id)::text = "
                "NULLIF(current_setting('app.organization_id', true), '')) "
                "WITH CHECK ((organization_id)::text = "
                "NULLIF(current_setting('app.organization_id', true), ''))"
            )
        )
    with engine.connect() as connection:
        planted = _relation_state(connection, schema_name, SHORT_BOUNDS_CHILD)
    assert _is_hardened(planted, SHORT_BOUNDS_CHILD), planted
    assert planted["bounds"] == "FOR VALUES FROM ('2027-01-01') TO ('2027-01-31')", planted

    case = _Case(
        parent="bi_fact_position_daily",
        function="bi_ensure_month_partition",
        probe=SHORT_BOUNDS_PROBE,
        child=SHORT_BOUNDS_CHILD,
        lower="2027-01-01",
        upper="2027-02-01",
        insert=_insert_daily,
    )
    outcome, value = _call_ensure(engine, case)

    assert outcome == "raised", (
        f"bi_ensure_month_partition returned {value!r} for a child bounded "
        "['2027-01-01','2027-01-31'), so 2027-01-31 routes to DEFAULT while the function "
        "reports January covered"
    )
    assert SHORT_BOUNDS_CHILD in value, value
    with engine.connect() as connection, connection.begin():
        _set_tenant(connection, ORG_1)
        _insert_daily(connection, ORG_1, BANK_A, date(2027, 1, 31))
        routed = connection.scalar(
            text(
                "SELECT tableoid::regclass::text FROM bi_fact_position_daily "
                "WHERE as_of_date = DATE '2027-01-31'"
            )
        )
    # The premise of the hazard, asserted rather than narrated.
    assert routed == "bi_fact_position_daily_default", routed


# --- (5) EXECUTE on the definer functions --------------------------------------


def test_execute_is_held_by_the_migrating_role_and_bypassrls_logins_and_nobody_else(
    adoption_schema: MigratedPostgresSchema,
) -> None:
    """The grant set the migration installs, read back from the ACL.

    ``202609220066`` revokes EXECUTE from PUBLIC and grants it to the migrating
    role plus every ``BYPASSRLS`` login role PRESENT AT MIGRATION TIME. That last
    clause is a deployment landmine (audit A360-4 INFO): a worker role created
    after the migration holds no grant, and ``query_log.record`` is deliberately
    unguarded, so exports and subscription deliveries under such a role fail with
    ``permission denied for function``. This test pins the installed set exactly —
    no PUBLIC, the owner, and precisely the roles the predicate names — and the
    next test pins the landmine itself so the runbook sentence is executable.
    """
    with adoption_schema.app_engine.connect() as connection:
        expected = {connection.scalar(text("SELECT current_user"))} | set(
            connection.execute(
                text(
                    "SELECT rolname FROM pg_roles WHERE rolname NOT LIKE 'pg\\_%' "
                    "AND NOT rolsuper AND rolbypassrls AND rolcanlogin"
                )
            ).scalars()
        )
        for signature in PARTITION_FUNCTIONS:
            grantees = connection.execute(
                text(
                    """
                    SELECT CASE WHEN a.grantee = 0 THEN 'PUBLIC' ELSE r.rolname END,
                           a.privilege_type
                      FROM pg_proc p
                      JOIN pg_namespace n ON n.oid = p.pronamespace
                      CROSS JOIN LATERAL aclexplode(p.proacl) AS a
                      LEFT JOIN pg_roles r ON r.oid = a.grantee
                     WHERE n.nspname = :schema_name AND p.oid = to_regprocedure(:signature)
                    """
                ),
                {
                    "schema_name": adoption_schema.schema_name,
                    "signature": f"{adoption_schema.schema_name}.{signature}",
                },
            ).all()
            executors = {grantee for grantee, privilege in grantees if privilege == "EXECUTE"}
            assert "PUBLIC" not in executors, f"{signature}: PUBLIC may execute"
            assert executors == expected, (
                f"{signature}: EXECUTE is held by {sorted(executors)}, the migration's "
                f"predicate names {sorted(expected)}"
            )


def test_a_role_created_after_the_migration_cannot_call_the_functions_until_granted(
    adoption_schema: MigratedPostgresSchema,
) -> None:
    """The landmine, executed: a later worker role is refused, then admitted by GRANT.

    A ``BYPASSRLS`` role cannot be created by a ``CREATEROLE``-only test role, so
    the later role here is a plain NOLOGIN one — the privilege check does not
    care, and neither does production: what admits a caller is the grant, not
    the attribute. ``tests/live_data`` carries the invariant that the real
    worker holds it.
    """
    engine = adoption_schema.app_engine
    schema_name = adoption_schema.schema_name
    role = f"bi_late_worker_{uuid4().hex[:12]}"
    with engine.connect() as admin:
        if not _temporary_non_owner_role(admin, role):
            _require_or_skip_privileges(
                "the TEST_DATABASE_URL role cannot create a second role (CREATEROLE), so a "
                "later worker role cannot be proven"
            )
    try:
        with engine.begin() as admin:
            admin.execute(text(f'GRANT USAGE ON SCHEMA "{schema_name}" TO {role}'))
        with (
            engine.connect() as connection,
            pytest.raises(DBAPIError) as refused,
            connection.begin(),
        ):
            connection.execute(text(f"SET LOCAL ROLE {role}"))
            connection.execute(
                text("SELECT bi_ensure_month_partition('bi_fact_loan_event', DATE '2030-01-01')")
            )
        assert "permission denied for function bi_ensure_month_partition" in str(refused.value)

        with engine.begin() as admin:
            for signature in PARTITION_FUNCTIONS:
                admin.execute(text(f"GRANT EXECUTE ON FUNCTION {signature} TO {role}"))
        with engine.connect() as connection, connection.begin():
            connection.execute(text(f"SET LOCAL ROLE {role}"))
            created = connection.scalar(
                text("SELECT bi_ensure_month_partition('bi_fact_loan_event', DATE '2030-01-01')")
            )
        assert created == "bi_fact_loan_event_y2030m01"
    finally:
        with engine.begin() as admin:
            for signature in PARTITION_FUNCTIONS:
                admin.execute(text(f"REVOKE ALL ON FUNCTION {signature} FROM {role}"))
            admin.execute(text(f'REVOKE ALL ON SCHEMA "{schema_name}" FROM {role}'))
            admin.execute(text(f"DROP ROLE {role}"))
