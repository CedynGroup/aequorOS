"""Postgres RLS proof for the partitioned BI marts (``202609220066``).

Row-level security on a partitioned table has a hole the ordinary per-table
proof (``test_current_financial_facts_rls.py``) cannot see: Postgres applies
the PARENT's policies to a query through the parent, but a partition addressed
by its own name has only its own policies — and Postgres does not copy any.
So a child created without ENABLE + FORCE + the tenant policy is a table every
tenant can read by naming it, while every test through the parent stays green.

This suite therefore asks the question three ways for each partitioned mart:
through the parent, DIRECTLY on a month partition created by
``bi_ensure_month_partition``, and DIRECTLY on the DEFAULT partition — and for
each, that another tenant sees zero rows and that no tenant at all sees zero
rows (fail closed). It seeds two tenants under their own GUC, exactly as the
current-facts proof does. Postgres-gated on ``TEST_DATABASE_URL`` and skipped
under a role that bypasses RLS, because such a role would prove nothing.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from alembic import command

# Imported for its side effect as much as for the name: app.db.session
# registers the ``after_begin`` listener that sets the tenant GUC.
from app.db.session import set_tenant_rls_context
from app.models.bi import BiFactPositionDaily
from tests.api.helpers import ORG_1, ORG_2
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

BANK_A = "BK-BIRLSA001"
BANK_B = "BK-BIRLSB002"
#: A month that gets its own child through the ensure function...
CHILD_MONTH = date(2026, 9, 1)
IN_CHILD = date(2026, 9, 18)
#: ...and a date no child covers, so its rows land in the DEFAULT partition.
IN_DEFAULT = date(2031, 2, 3)

#: (parent, child created for CHILD_MONTH, DEFAULT partition) per mart.
PARTITIONED_MARTS: tuple[tuple[str, str, str], ...] = tuple(
    (parent, f"{parent}_y2026m09", f"{parent}_default")
    for parent in (
        "bi_fact_position_daily",
        "bi_agg_position_daily",
        "bi_fact_loan_event",
        "bi_query_log",
    )
)


def _set_tenant(connection: Connection, organization_id: str | None) -> None:
    connection.execute(
        text("SELECT set_config('app.organization_id', :organization_id, true)"),
        {"organization_id": "" if organization_id is None else organization_id},
    )


def _insert_position(connection: Connection, org: str, bank: str, as_of: date) -> None:
    connection.execute(
        text(
            """
            INSERT INTO bi_fact_position_daily
              (as_of_date, snapshot_id, position_id, organization_id, bank_id, source_system,
               source_reference, position_type, currency, balance_native, balance_rc,
               fx_unconverted, builder_version, built_at)
            VALUES
              (:as_of, :snapshot_id, :position_id, :org, :bank, 'API_PUSH', :reference,
               'LOAN', 'GHS', :balance, :balance, false, 1, now())
            """
        ),
        {
            "as_of": as_of,
            "snapshot_id": str(uuid4()),
            "position_id": str(uuid4()),
            "org": org,
            "bank": bank,
            "reference": f"{bank}:{as_of.isoformat()}",
            "balance": Decimal("1000.000000"),
        },
    )


def _insert_aggregate(connection: Connection, org: str, bank: str, as_of: date) -> None:
    connection.execute(
        text(
            """
            INSERT INTO bi_agg_position_daily
              (as_of_date, id, organization_id, bank_id, position_type, currency, row_count,
               balance_rc_sum, classification_exposure_rc_sum, non_performing_exposure_rc_sum,
               provision_required_rc_sum, provision_held_rc_sum, collateral_rc_sum,
               rate_x_balance_rc_sum, fx_unconverted_count, builder_version, built_at)
            VALUES
              (:as_of, :id, :org, :bank, 'LOAN', 'GHS', 1, 1000, 1000, 0, 0, 0, 0, 0, 0, 1, now())
            """
        ),
        {"as_of": as_of, "id": str(uuid4()), "org": org, "bank": bank},
    )


def _insert_loan_event(connection: Connection, org: str, bank: str, event_date: date) -> None:
    connection.execute(
        text(
            """
            INSERT INTO bi_fact_loan_event
              (event_date, event_id, organization_id, bank_id, event_type, source_system,
               source_reference, position_source_reference, amount_native, currency,
               fx_unconverted, attribution_basis, builder_version, built_at)
            VALUES
              (:event_date, :event_id, :org, :bank, 'REPAYMENT', 'API_PUSH', :reference,
               :reference, 10, 'GHS', false, 'unmatched', 1, now())
            """
        ),
        {
            "event_date": event_date,
            "event_id": str(uuid4()),
            "org": org,
            "bank": bank,
            "reference": f"{bank}:{event_date.isoformat()}",
        },
    )


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
            "query_hash": "b" * 64,
        },
    )


def _seed_tenant(connection: Connection, *, organization_id: str, bank_id: str) -> None:
    """One org + bank, then one row per mart in the month child AND in DEFAULT,
    all under that tenant's own GUC (``organizations``/``banks`` are FORCE-RLS)."""
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
        for day in (IN_CHILD, IN_DEFAULT):
            _insert_position(connection, organization_id, bank_id, day)
            _insert_aggregate(connection, organization_id, bank_id, day)
            _insert_loan_event(connection, organization_id, bank_id, day)
            _insert_query_log(connection, organization_id, bank_id, day)


@pytest.fixture(scope="module")
def rls_schema() -> Iterator[MigratedPostgresSchema]:
    """A migrated disposable schema with the month children created and two
    tenants' rows in both the children and the DEFAULT partitions."""
    test_database_url = os.environ["TEST_DATABASE_URL"]
    schema_name = f"risk_service_bi_rls_{uuid4().hex}"
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
            role_attributes = connection.execute(
                text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
            ).one()
            connection.rollback()
            if role_attributes[0] or role_attributes[1]:
                pytest.skip("Current TEST_DATABASE_URL role bypasses RLS.")
            with connection.begin():
                for parent, _child, _default in PARTITIONED_MARTS:
                    connection.execute(
                        text("SELECT bi_ensure_month_partition(:parent, :month)"),
                        {"parent": parent, "month": CHILD_MONTH},
                    )
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


def _count(connection: Connection, relation: str, *, organization_id: str | None) -> int:
    with connection.begin():
        _set_tenant(connection, organization_id)
        return int(connection.scalar(text(f"SELECT count(*) FROM {relation}")))


def test_rows_were_routed_to_the_child_and_to_default(rls_schema: MigratedPostgresSchema) -> None:
    """The fixture is only a proof if a row really sits in each partition."""
    with rls_schema.app_engine.connect() as connection:
        for parent, child, default in PARTITIONED_MARTS:
            with connection.begin():
                _set_tenant(connection, ORG_1)
                routed = {
                    key: value
                    for key, value in connection.execute(
                        text(
                            f"SELECT tableoid::regclass::text, count(*) FROM {parent} "
                            "GROUP BY tableoid"
                        )
                    ).tuples()
                }
            assert routed == {child: 1, default: 1}, f"{parent}: {routed}"


@pytest.mark.parametrize(("parent", "child", "default"), PARTITIONED_MARTS)
def test_a_tenant_sees_only_its_own_rows_through_every_path(
    rls_schema: MigratedPostgresSchema, parent: str, child: str, default: str
) -> None:
    with rls_schema.app_engine.connect() as connection:
        own = {
            relation: _count(connection, relation, organization_id=ORG_1)
            for relation in (parent, child, default)
        }
        with connection.begin():
            _set_tenant(connection, ORG_1)
            foreign_rows = connection.scalar(
                text(f"SELECT count(*) FROM {parent} WHERE organization_id = :other"),
                {"other": ORG_2},
            )
    assert own == {parent: 2, child: 1, default: 1}
    assert foreign_rows == 0


@pytest.mark.parametrize(("parent", "child", "default"), PARTITIONED_MARTS)
def test_cross_tenant_reads_return_zero_rows_on_parent_child_and_default(
    rls_schema: MigratedPostgresSchema, parent: str, child: str, default: str
) -> None:
    """Tenant B, addressing tenant A's rows by every path, gets nothing —
    including by naming the partition directly, which is the hole."""
    with rls_schema.app_engine.connect() as connection, connection.begin():
        _set_tenant(connection, ORG_2)
        seen_by_b = {
            relation: int(
                connection.scalar(
                    text(f"SELECT count(*) FROM {relation} WHERE organization_id = :other"),
                    {"other": ORG_1},
                )
            )
            for relation in (parent, child, default)
        }
    assert seen_by_b == {parent: 0, child: 0, default: 0}


@pytest.mark.parametrize(("parent", "child", "default"), PARTITIONED_MARTS)
def test_no_tenant_context_fails_closed_on_parent_child_and_default(
    rls_schema: MigratedPostgresSchema, parent: str, child: str, default: str
) -> None:
    with rls_schema.app_engine.connect() as connection:
        with connection.begin():
            unset = {
                relation: int(connection.scalar(text(f"SELECT count(*) FROM {relation}")))
                for relation in (parent, child, default)
            }
        empty = {
            relation: _count(connection, relation, organization_id=None)
            for relation in (parent, child, default)
        }
    assert unset == {parent: 0, child: 0, default: 0}
    assert empty == {parent: 0, child: 0, default: 0}


def test_cross_tenant_insert_is_refused_by_with_check(rls_schema: MigratedPostgresSchema) -> None:
    """Tenant A cannot plant a row wearing tenant B's label, through the parent
    or directly into a partition."""
    for relation in ("bi_fact_position_daily", "bi_fact_position_daily_y2026m09"):
        with (
            rls_schema.app_engine.connect() as connection,
            pytest.raises(DBAPIError) as refused,
            connection.begin(),
        ):
            _set_tenant(connection, ORG_1)
            connection.execute(
                text(
                    f"""
                    INSERT INTO {relation}
                      (as_of_date, snapshot_id, position_id, organization_id, bank_id,
                       source_system, source_reference, position_type, currency,
                       balance_native, fx_unconverted, builder_version, built_at)
                    VALUES
                      (:as_of, :snapshot_id, :position_id, :org, :bank, 'API_PUSH', 'planted',
                       'LOAN', 'GHS', 1, false, 1, now())
                    """
                ),
                {
                    "as_of": IN_CHILD,
                    "snapshot_id": str(uuid4()),
                    "position_id": str(uuid4()),
                    "org": ORG_2,
                    "bank": BANK_B,
                },
            )
        assert "row-level security policy" in str(refused.value), relation


def test_the_orm_session_hook_scopes_a_bi_read(rls_schema: MigratedPostgresSchema) -> None:
    """The product's plumbing — ``session.info['organization_id']`` turned into
    the GUC by the global ``after_begin`` listener — is what the BI session
    factory relies on too, so reading the mart model through it is the
    end-to-end check."""
    with Session(bind=rls_schema.app_engine) as tenant_session:
        tenant_session.info["organization_id"] = ORG_2
        rows = tenant_session.scalars(select(BiFactPositionDaily)).all()
    with Session(bind=rls_schema.app_engine) as anonymous_session:
        anonymous = anonymous_session.scalars(select(BiFactPositionDaily)).all()

    assert sorted(row.as_of_date for row in rows) == [IN_CHILD, IN_DEFAULT]
    assert {row.organization_id for row in rows} == {ORG_2}
    assert anonymous == []
