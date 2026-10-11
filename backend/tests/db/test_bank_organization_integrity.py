"""Bank scope is a database contract on the migrated schema."""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import date
from typing import Protocol, cast
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from psycopg.errors import ForeignKeyViolation
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from app.db.base import Base
from tests.db.test_postgres_migrations import (
    MigratedPostgresSchema,
    alembic_config_for_app,
    migrate_isolated_postgres_schema,
)

pytestmark = [
    pytest.mark.committing_db,
    pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="PostgreSQL is required"),
]
TABLES = (
    "capital_plans",
    "ilaap_snapshots",
    "liquidity_ewi_indicators",
    "contingency_funding_plans",
    "cfp_activation_events",
    "market_data_source_preferences",
    "stress_scenarios",
    "macro_scenarios",
    "management_action_plans",
)
ORG = "OR-INTEGRITY-A"
OTHER_ORG = "OR-INTEGRITY-B"
BANK = "BK-INTEGRITY-A"
OTHER_BANK = "BK-INTEGRITY-B"


class _BankScopeMigration(Protocol):
    def upgrade(self) -> None: ...

    def downgrade(self) -> None: ...


@pytest.fixture(scope="module")
def bank_integrity_schema() -> Iterator[MigratedPostgresSchema]:
    with pytest.MonkeyPatch.context() as monkeypatch:
        yield from migrate_isolated_postgres_schema(monkeypatch, downgrade=False)


def bank_scope_scenario(connection: Connection) -> None:
    """Two existing banks owned by different organizations."""
    for organization, bank in ((ORG, BANK), (OTHER_ORG, OTHER_BANK)):
        connection.execute(
            sa.text("SELECT set_config('app.organization_id', :org, true)"), {"org": organization}
        )
        connection.execute(
            Base.metadata.tables["organizations"]
            .insert()
            .values(id=organization, name="Scope proof")
        )
        connection.execute(
            Base.metadata.tables["banks"]
            .insert()
            .values(
                id=bank,
                organization_id=organization,
                name="Scope proof",
                short_name="Scope",
                currency="GHS",
                jurisdiction_code="GH",
                license_type="bank",
                institution_type="universal_bank",
            )
        )
    connection.execute(
        sa.text("SELECT set_config('app.organization_id', :org, true)"), {"org": ORG}
    )


def scoped_row(table: str, bank: str | None) -> dict[str, object]:
    """A valid row except for the caller-selected bank scope."""
    row: dict[str, object] = {"organization_id": ORG, "bank_id": bank}
    if table in ("capital_plans", "contingency_funding_plans"):
        row.update(version=1, status="draft", content={})
    elif table == "ilaap_snapshots":
        row.update(
            reporting_period_id=uuid4(), as_of_date=date(2026, 6, 30), content={}, adequate=True
        )
    elif table == "liquidity_ewi_indicators":
        row.update(code="PROOF")
    elif table == "cfp_activation_events":
        row.update(cfp_id=uuid4(), event_type="activated", reason="proof", ewi_snapshot=[])
    elif table == "stress_scenarios":
        row.update(module="liquidity", code="PROOF", name="Proof", shocks={})
    elif table == "macro_scenarios":
        row.update(code="PROOF", name="Proof", scenario_type="adverse")
    elif table == "management_action_plans":
        row.update(code="PROOF", name="Proof")
    return row


def row_with_parents(connection: Connection, table: str, bank: str | None) -> dict[str, object]:
    row = scoped_row(table, bank)
    if table == "cfp_activation_events":
        parent = Base.metadata.tables["contingency_funding_plans"]
        row["cfp_id"] = connection.execute(
            parent.insert().values(**scoped_row(parent.name, BANK)).returning(parent.c.id)
        ).scalar_one()
    return row


@pytest.mark.parametrize("table", TABLES)
def test_a_bank_owned_by_another_organization_is_refused(
    bank_integrity_schema: MigratedPostgresSchema,
    table: str,
) -> None:
    with (
        bank_integrity_schema.app_engine.connect() as connection,
        connection.begin() as transaction,
    ):
        bank_scope_scenario(connection)
        row = row_with_parents(connection, table, OTHER_BANK)
        with pytest.raises(IntegrityError) as failure:
            connection.execute(Base.metadata.tables[table].insert().values(**row))
        database_error = failure.value.orig
        assert isinstance(database_error, ForeignKeyViolation)
        assert database_error.diag.constraint_name == f"fk_{table}_org_bank"
        transaction.rollback()


@pytest.mark.parametrize("table", TABLES)
def test_a_bank_owned_by_the_same_organization_is_admitted(
    bank_integrity_schema: MigratedPostgresSchema,
    table: str,
) -> None:
    with (
        bank_integrity_schema.app_engine.connect() as connection,
        connection.begin() as transaction,
    ):
        bank_scope_scenario(connection)
        result = connection.execute(
            Base.metadata.tables[table]
            .insert()
            .values(**row_with_parents(connection, table, BANK))
            .returning(Base.metadata.tables[table].c.id)
        )
        assert result.scalar_one() is not None
        transaction.rollback()


@pytest.mark.parametrize("table", ["macro_scenarios", "management_action_plans"])
def test_an_organization_wide_scope_remains_admitted(
    bank_integrity_schema: MigratedPostgresSchema,
    table: str,
) -> None:
    with (
        bank_integrity_schema.app_engine.connect() as connection,
        connection.begin() as transaction,
    ):
        bank_scope_scenario(connection)
        result = connection.execute(
            Base.metadata.tables[table]
            .insert()
            .values(**scoped_row(table, None))
            .returning(Base.metadata.tables[table].c.id)
        )
        assert result.scalar_one() is not None
        transaction.rollback()


def test_bank_foreign_keys_match_themigrate_isolated_postgres_schema(
    bank_integrity_schema: MigratedPostgresSchema,
) -> None:
    inspector = sa.inspect(bank_integrity_schema.app_engine)
    for name in TABLES:
        model = {
            (
                tuple(element.parent.name for element in fk.elements),
                tuple(element.column.name for element in fk.elements),
                fk.referred_table.name,
                fk.ondelete,
            )
            for fk in Base.metadata.tables[name].foreign_key_constraints
        }
        migrated = {
            (
                tuple(fk["constrained_columns"]),
                tuple(fk["referred_columns"]),
                fk["referred_table"],
                fk.get("options", {}).get("ondelete"),
            )
            for fk in inspector.get_foreign_keys(name, schema=bank_integrity_schema.schema_name)
        }
        assert model == migrated, name


@pytest.mark.parametrize("table", TABLES)
def test_existing_mixed_ownership_fails_verification_across_tenants(
    bank_integrity_schema: MigratedPostgresSchema,
    table: str,
) -> None:
    revision = ScriptDirectory.from_config(alembic_config_for_app()).get_revision("202610110093")
    assert revision is not None
    migration = cast(_BankScopeMigration, revision.module)
    with (
        bank_integrity_schema.app_engine.connect() as connection,
        connection.begin() as transaction,
    ):
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
            bank_scope_scenario(connection)
            connection.execute(
                Base.metadata.tables[table]
                .insert()
                .values(**row_with_parents(connection, table, OTHER_BANK))
            )
            connection.execute(sa.text("SELECT set_config('app.organization_id', '', true)"))
            with pytest.raises(RuntimeError, match=f"{table}: 1 bank scopes violate"):
                migration.upgrade()
        transaction.rollback()
