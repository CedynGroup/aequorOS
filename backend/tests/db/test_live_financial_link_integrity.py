"""Live UUID links cannot commit outside the owning organization and bank."""

from __future__ import annotations

from datetime import date
from typing import cast
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from psycopg.errors import ForeignKeyViolation
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from app.db.base import Base
from tests.db.test_bank_organization_integrity import BANK, ORG, OTHER_BANK, OTHER_ORG, pytestmark
from tests.db.test_canonical_bank_integrity import (
    AS_OF,
    CANONICAL_TABLES,
    SIBLING_BANK,
    CanonicalBankMigration,
    canonical_row,
    insert_row,
    sibling_banks_scenario,
)
from tests.db.test_canonical_migration_structural_parity import bank_integrity_schema
from tests.db.test_postgres_migrations import (
    MigratedPostgresSchema,
    alembic_config_for_app,
    forward_migrated_postgres_schema,
)

__all__ = ["bank_integrity_schema", "forward_migrated_postgres_schema", "pytestmark"]
LIVE_TABLES = tuple(table for table in CANONICAL_TABLES if table != "canonical_reference_rows")
REFERENCES = (
    *((table, "superseded_by", table) for table in LIVE_TABLES),
    *((table, "withdrawn_by_batch_id", "ingestion_batches") for table in LIVE_TABLES),
    ("ilaap_snapshots", "reporting_period_id", "bank_reporting_periods"),
)


def live_row(
    connection: Connection, table: str, bank: str, organization: str = ORG
) -> dict[str, object]:
    if table == "ilaap_snapshots":
        return dict(
            organization_id=organization,
            bank_id=bank,
            reporting_period_id=uuid4(),
            as_of_date=AS_OF,
            content={},
            adequate=True,
        )
    if table == "bank_reporting_periods":
        return dict(
            organization_id=organization,
            bank_id=bank,
            period_start=date(2026, 4, 1),
            period_end=AS_OF,
            label="Scope proof",
            status="open",
        )
    return canonical_row(connection, table, bank, organization)


def proposed_link(
    connection: Connection, table: str, column: str, parent: str, scope: str
) -> dict[str, object]:
    row = live_row(connection, table, BANK)
    if scope == "missing":
        row[column] = uuid4()
    else:
        organization, bank = (
            (OTHER_ORG, OTHER_BANK)
            if scope == "other_organization"
            else (ORG, SIBLING_BANK if scope == "sibling_bank" else BANK)
        )
        connection.execute(
            sa.text("SELECT set_config('app.organization_id', :org, true)"), {"org": organization}
        )
        row[column] = insert_row(
            connection, parent, live_row(connection, parent, bank, organization)
        )
        connection.execute(
            sa.text("SELECT set_config('app.organization_id', :org, true)"), {"org": ORG}
        )
    return row


@pytest.mark.parametrize(("table", "column", "parent"), REFERENCES)
@pytest.mark.parametrize("scope", ["missing", "sibling_bank", "other_organization"])
def test_an_invalid_live_link_cannot_be_committed(
    bank_integrity_schema: MigratedPostgresSchema,
    table: str,
    column: str,
    parent: str,
    scope: str,
) -> None:
    with (
        bank_integrity_schema.app_engine.connect() as connection,
        connection.begin() as transaction,
    ):
        sibling_banks_scenario(connection)
        row = proposed_link(connection, table, column, parent, scope)
        with pytest.raises(IntegrityError) as failure:
            insert_row(connection, table, row)
            transaction.commit()
        database_error = failure.value.orig
        assert isinstance(database_error, ForeignKeyViolation)
        assert database_error.diag.constraint_name == f"fk_{table}_{column}_bank"
        connection.rollback()


@pytest.mark.parametrize(("table", "column", "parent"), REFERENCES)
def test_a_live_link_within_its_own_bank_is_admitted(
    bank_integrity_schema: MigratedPostgresSchema,
    table: str,
    column: str,
    parent: str,
) -> None:
    with (
        bank_integrity_schema.app_engine.connect() as connection,
        connection.begin() as transaction,
    ):
        sibling_banks_scenario(connection)
        row = proposed_link(connection, table, column, parent, "own_bank")
        result = insert_row(connection, table, row)
        # Validate deferred supersession now without committing fixture data.
        connection.execute(sa.text("SET CONSTRAINTS ALL IMMEDIATE"))
        assert isinstance(result, UUID)
        transaction.rollback()


@pytest.mark.parametrize(("table", "column", "parent"), REFERENCES)
def test_existing_missing_live_links_stop_the_migration(
    bank_integrity_schema: MigratedPostgresSchema,
    table: str,
    column: str,
    parent: str,
) -> None:
    revision = ScriptDirectory.from_config(alembic_config_for_app()).get_revision("202610110097")
    assert revision is not None
    migration = cast(CanonicalBankMigration, revision.module)
    with (
        bank_integrity_schema.app_engine.connect() as connection,
        connection.begin() as transaction,
    ):
        sibling_banks_scenario(connection)
        row = proposed_link(connection, table, column, parent, "missing")
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
            insert_row(connection, table, row)
            connection.execute(sa.text("SELECT set_config('app.organization_id', '', true)"))
            with pytest.raises(RuntimeError, match=f"{table}.{column}: 1 invalid live"):
                migration.upgrade()
        transaction.rollback()


def test_supersession_can_commit_when_the_replacement_is_inserted_after_retirement(
    forward_migrated_postgres_schema: MigratedPostgresSchema,
) -> None:
    schema = forward_migrated_postgres_schema
    table = Base.metadata.tables["canonical_positions"]
    replacement_id = uuid4()
    with schema.app_engine.begin() as connection:
        sibling_banks_scenario(connection)
        original_id = insert_row(connection, table.name, live_row(connection, table.name, BANK))
        replacement = live_row(connection, table.name, BANK)
        replacement["id"] = replacement_id
    with schema.app_engine.begin() as connection:
        connection.execute(
            sa.text("SELECT set_config('app.organization_id', :org, true)"), {"org": ORG}
        )
        connection.execute(
            table.update().where(table.c.id == original_id).values(superseded_by=replacement_id)
        )
        insert_row(connection, table.name, replacement)
    with schema.app_engine.connect() as connection:
        connection.execute(
            sa.text("SELECT set_config('app.organization_id', :org, true)"), {"org": ORG}
        )
        assert (
            cast(
                UUID,
                connection.scalar(
                    sa.select(table.c.superseded_by).where(table.c.id == original_id)
                ),
            )
            == replacement_id
        )
        assert (
            cast(UUID, connection.scalar(sa.select(table.c.id).where(table.c.id == replacement_id)))
            == replacement_id
        )
