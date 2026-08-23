"""Postgres RLS proof for ``current_financial_facts`` (audit P0-1).

The live Treasury plane's current fact set carries ``organization_id`` and holds
every tenant's balance-sheet/capital/liquidity inputs, but migration
``202608190021`` created it without row-level security — the one tenancy-carrying
fact table on the primary reading ``relrowsecurity = false``. ``202608220027``
closes that gap and these tests are its executable proof.

They MUST run on Postgres: the hermetic suite is SQLite (no RLS at all), and the
``db_session``/``db_client`` fixtures build their schema with
``Base.metadata.create_all`` rather than the migration chain, so RLS behaviour is
invisible to them either way. Opt in with ``TEST_DATABASE_URL`` — a disposable
schema is created and dropped per module, exactly as
``tests/db/test_postgres_migrations.py`` does.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from alembic import command

# Imported for its side effect as much as for the model: app.db.session registers
# the ``after_begin`` listener that sets the tenant GUC from ``session.info``.
from app.db.session import set_tenant_rls_context
from app.models import CurrentFinancialFact
from tests.api.helpers import ORG_1, ORG_2
from tests.db.test_postgres_migrations import (
    MigratedPostgresSchema,
    alembic_config_for_app,
    clear_database_caches,
    postgres_schema_url,
)

# The import above is load-bearing for its side effect; keep it referenced.
_ = set_tenant_rls_context

pytestmark = pytest.mark.skipif(
    os.getenv("TEST_DATABASE_URL") is None,
    reason="TEST_DATABASE_URL is required for Postgres RLS tests.",
)

TABLE = "current_financial_facts"
POLICY = f"{TABLE}_tenant_isolation"
# 202608220027 is the migration under test; its parent is the rewind target.
DOWN_REVISION = "202608210026"

BANK_A = "BK-RLSAA001"
BANK_B = "BK-RLSBB002"
FACT_A = str(uuid4())
FACT_B = str(uuid4())
AMOUNT_A = Decimal("1000000.0000")
AMOUNT_B = Decimal("7777.0000")


def _set_tenant(connection, organization_id: str | None) -> None:
    connection.execute(
        text("SELECT set_config('app.organization_id', :organization_id, true)"),
        {"organization_id": "" if organization_id is None else organization_id},
    )


def _seed_tenant(
    connection,
    *,
    organization_id: str,
    bank_id: str,
    fact_id: str,
    amount: Decimal,
) -> None:
    """Insert one org + bank + current fact, under that tenant's own GUC.

    ``organizations`` and ``banks`` are themselves FORCE-RLS, so each tenant's
    rows have to be written while its own GUC is in force.
    """
    with connection.begin():
        _set_tenant(connection, organization_id)
        connection.execute(
            text(
                """
                INSERT INTO organizations (id, name, created_at, updated_at)
                VALUES (:organization_id, :name, now(), now())
                """
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
        connection.execute(
            text(
                f"""
                INSERT INTO {TABLE}
                  (id, organization_id, bank_id, source_as_of_date, source_generation,
                   fact_group, category, amount, currency, created_at, updated_at)
                VALUES
                  (:fact_id, :organization_id, :bank_id, DATE '2026-08-21', 1,
                   'balance_sheet', 'total_assets', :amount, 'GHS', now(), now())
                """
            ),
            {
                "fact_id": fact_id,
                "organization_id": organization_id,
                "bank_id": bank_id,
                "amount": amount,
            },
        )


@pytest.fixture(scope="module")
def rls_schema() -> Iterator[MigratedPostgresSchema]:
    """A migrated disposable schema seeded with two tenants' current facts.

    Module-scoped: the migration chain is long and every test below reads the
    same two-tenant fixture. The downgrade test restores head before yielding
    control back.
    """
    test_database_url = os.environ["TEST_DATABASE_URL"]
    schema_name = f"risk_service_cff_rls_{uuid4().hex}"
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
            connection.rollback()  # close the autobegun read transaction
            if role_attributes[0] or role_attributes[1]:
                pytest.skip("Current TEST_DATABASE_URL role bypasses RLS.")
            _seed_tenant(
                connection,
                organization_id=ORG_1,
                bank_id=BANK_A,
                fact_id=FACT_A,
                amount=AMOUNT_A,
            )
            _seed_tenant(
                connection,
                organization_id=ORG_2,
                bank_id=BANK_B,
                fact_id=FACT_B,
                amount=AMOUNT_B,
            )
        yield MigratedPostgresSchema(app_engine=app_engine, schema_name=schema_name)
    finally:
        monkeypatch.undo()
        clear_database_caches()
        app_engine.dispose()
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        admin_engine.dispose()


def test_current_financial_facts_rls_is_enabled_and_forced(
    rls_schema: MigratedPostgresSchema,
) -> None:
    """ENABLE alone is not enough: the app role owns the table, so without FORCE
    Postgres exempts it from its own policy."""
    with rls_schema.app_engine.connect() as connection:
        row_security = connection.execute(
            text(
                """
                SELECT c.relrowsecurity, c.relforcerowsecurity
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = :schema_name AND c.relname = :table
                """
            ),
            {"schema_name": rls_schema.schema_name, "table": TABLE},
        ).one()
    assert row_security == (True, True)
    assert rls_schema.policies({TABLE}) == {POLICY}


def test_current_financial_facts_rls_hides_other_tenant_rows(
    rls_schema: MigratedPostgresSchema,
) -> None:
    with rls_schema.app_engine.connect() as connection, connection.begin():
        _set_tenant(connection, ORG_1)
        visible_to_a = (
            connection.execute(text(f"SELECT id FROM {TABLE} ORDER BY id")).scalars().all()
        )
        cross_tenant_row = connection.scalar(
            text(f"SELECT count(*) FROM {TABLE} WHERE id = :fact_id"),
            {"fact_id": FACT_B},
        )

    assert [str(row) for row in visible_to_a] == [FACT_A]
    assert cross_tenant_row == 0


def test_current_financial_facts_rls_blocks_cross_tenant_update_and_delete(
    rls_schema: MigratedPostgresSchema,
) -> None:
    with rls_schema.app_engine.connect() as connection:
        with connection.begin():
            _set_tenant(connection, ORG_1)
            updated = connection.execute(
                text(f"UPDATE {TABLE} SET amount = 1 WHERE id = :fact_id"),
                {"fact_id": FACT_B},
            ).rowcount
            deleted = connection.execute(
                text(f"DELETE FROM {TABLE} WHERE id = :fact_id"),
                {"fact_id": FACT_B},
            ).rowcount

        with connection.begin():
            _set_tenant(connection, ORG_2)
            surviving_amount = connection.scalar(
                text(f"SELECT amount FROM {TABLE} WHERE id = :fact_id"),
                {"fact_id": FACT_B},
            )

    assert updated == 0
    assert deleted == 0
    assert surviving_amount == AMOUNT_B


def test_current_financial_facts_rls_blocks_cross_tenant_insert(
    rls_schema: MigratedPostgresSchema,
) -> None:
    """WITH CHECK: tenant A cannot plant a row wearing tenant B's label."""
    with (
        rls_schema.app_engine.connect() as connection,
        pytest.raises(DBAPIError) as excinfo,
        connection.begin(),
    ):
        _set_tenant(connection, ORG_1)
        connection.execute(
            text(
                f"""
                INSERT INTO {TABLE}
                  (id, organization_id, bank_id, source_as_of_date, source_generation,
                   fact_group, category, amount, currency, created_at, updated_at)
                VALUES
                  (:fact_id, :organization_id, :bank_id, DATE '2026-08-21', 1,
                   'balance_sheet', 'planted', 1, 'GHS', now(), now())
                """
            ),
            {"fact_id": str(uuid4()), "organization_id": ORG_2, "bank_id": BANK_B},
        )

    assert "row-level security policy" in str(excinfo.value)


def test_current_financial_facts_rls_excludes_other_tenants_from_aggregates(
    rls_schema: MigratedPostgresSchema,
) -> None:
    """An aggregate is the leak that a WHERE-clause audit misses: the row is
    never returned, but its amount lands in the total."""
    with rls_schema.app_engine.connect() as connection, connection.begin():
        _set_tenant(connection, ORG_1)
        count_for_a = connection.scalar(text(f"SELECT count(*) FROM {TABLE}"))
        sum_for_a = connection.scalar(text(f"SELECT sum(amount) FROM {TABLE}"))
        max_for_a = connection.scalar(text(f"SELECT max(amount) FROM {TABLE}"))

    assert count_for_a == 1
    assert sum_for_a == AMOUNT_A
    assert max_for_a == AMOUNT_A


def test_current_financial_facts_rls_admits_the_owning_tenant(
    rls_schema: MigratedPostgresSchema,
) -> None:
    """The policy must not be a blanket deny: the owning tenant reads, inserts,
    updates and deletes its own rows."""
    own_fact_id = str(uuid4())
    with rls_schema.app_engine.connect() as connection:
        with connection.begin():
            _set_tenant(connection, ORG_2)
            connection.execute(
                text(
                    f"""
                    INSERT INTO {TABLE}
                      (id, organization_id, bank_id, source_as_of_date, source_generation,
                       fact_group, category, amount, currency, created_at, updated_at)
                    VALUES
                      (:fact_id, :organization_id, :bank_id, DATE '2026-08-21', 2,
                       'capital_component', 'cet1', 250, 'GHS', now(), now())
                    """
                ),
                {"fact_id": own_fact_id, "organization_id": ORG_2, "bank_id": BANK_B},
            )
            readable = connection.scalar(
                text(f"SELECT amount FROM {TABLE} WHERE id = :fact_id"),
                {"fact_id": own_fact_id},
            )
            updated = connection.execute(
                text(f"UPDATE {TABLE} SET amount = 300 WHERE id = :fact_id"),
                {"fact_id": own_fact_id},
            ).rowcount
            deleted = connection.execute(
                text(f"DELETE FROM {TABLE} WHERE id = :fact_id"),
                {"fact_id": own_fact_id},
            ).rowcount

        with connection.begin():
            _set_tenant(connection, ORG_2)
            remaining = (
                connection.execute(text(f"SELECT id FROM {TABLE} ORDER BY id")).scalars().all()
            )

    assert readable == Decimal("250.0000")
    assert updated == 1
    assert deleted == 1
    assert [str(row) for row in remaining] == [FACT_B]


def test_current_financial_facts_rls_fails_closed_without_tenant_context(
    rls_schema: MigratedPostgresSchema,
) -> None:
    """No GUC at all (and the empty-string form the session hook can leave)
    must both yield zero rows, never every tenant's."""
    with rls_schema.app_engine.connect() as connection:
        with connection.begin():
            unset_guc_rows = connection.scalar(text(f"SELECT count(*) FROM {TABLE}"))
        with connection.begin():
            _set_tenant(connection, None)
            empty_guc_rows = connection.scalar(text(f"SELECT count(*) FROM {TABLE}"))

    assert unset_guc_rows == 0
    assert empty_guc_rows == 0


def test_current_financial_facts_rls_admits_the_app_session_hook(
    rls_schema: MigratedPostgresSchema,
) -> None:
    """The policy must be satisfied by the plumbing the product actually uses.

    ``app/db/session.py::set_tenant_rls_context`` is the ``after_begin`` hook
    that turns ``session.info['organization_id']`` into the GUC — the single
    mechanism behind ``get_tenant_db_session`` (API) and ``worker._new_session``
    (``pipeline_refresh`` / ``official_run``). Reading the ORM model through it
    is the end-to-end check that no live Treasury calculation loses its inputs.
    """
    with Session(bind=rls_schema.app_engine) as tenant_session:
        tenant_session.info["organization_id"] = ORG_1
        via_hook = tenant_session.scalars(select(CurrentFinancialFact)).all()

    with Session(bind=rls_schema.app_engine) as anonymous_session:
        anonymous = anonymous_session.scalars(select(CurrentFinancialFact)).all()

    assert [str(fact.id) for fact in via_hook] == [FACT_A]
    assert [fact.organization_id for fact in via_hook] == [ORG_1]
    assert anonymous == []


def test_current_financial_facts_rls_downgrade_removes_policy(
    rls_schema: MigratedPostgresSchema,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The migration is reversible, and the (False, False) it leaves behind is
    exactly the pre-fix state the audit found on the primary.

    Runs last: it rewinds past 202608220027 and restores head, so the shared
    module schema is left as it was found. It targets that revision's PARENT by
    name rather than the relative ``-1`` — sibling remediations land on top of
    this one, so ``-1`` would revert somebody else's migration instead."""
    # conftest's autouse fixture blanks DATABASE_URL per test (the .env-leak
    # guard) and runs AFTER this module-scoped schema fixture, so point Alembic
    # back at the disposable schema for the duration of this test.
    monkeypatch.setenv(
        "DATABASE_URL", rls_schema.app_engine.url.render_as_string(hide_password=False)
    )
    clear_database_caches()
    alembic_config = alembic_config_for_app()
    command.downgrade(alembic_config, DOWN_REVISION)
    try:
        with rls_schema.app_engine.connect() as connection:
            after_downgrade = connection.execute(
                text(
                    """
                    SELECT c.relrowsecurity, c.relforcerowsecurity
                    FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = :schema_name AND c.relname = :table
                    """
                ),
                {"schema_name": rls_schema.schema_name, "table": TABLE},
            ).one()
        assert after_downgrade == (False, False)
        assert rls_schema.policies({TABLE}) == set()
    finally:
        command.upgrade(alembic_config, "head")

    with rls_schema.app_engine.connect() as connection:
        after_upgrade = connection.execute(
            text(
                """
                SELECT c.relrowsecurity, c.relforcerowsecurity
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = :schema_name AND c.relname = :table
                """
            ),
            {"schema_name": rls_schema.schema_name, "table": TABLE},
        ).one()
    assert after_upgrade == (True, True)
    assert rls_schema.policies({TABLE}) == {POLICY}
