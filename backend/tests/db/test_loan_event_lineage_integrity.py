"""A real-schema loan event requires an existing lineage in the same bank."""

from __future__ import annotations

from typing import cast
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from psycopg.errors import ForeignKeyViolation
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from tests.db.test_bank_organization_integrity import BANK, pytestmark
from tests.db.test_canonical_bank_integrity import (
    SIBLING_BANK,
    CanonicalBankMigration,
    canonical_row,
    insert_row,
    sibling_banks_scenario,
)
from tests.db.test_canonical_migration_structural_parity import bank_integrity_schema
from tests.db.test_postgres_migrations import MigratedPostgresSchema, alembic_config_for_app

__all__ = ["bank_integrity_schema", "pytestmark"]


@pytest.mark.parametrize("lineage", ["missing", "sibling_bank"])
def test_a_loan_event_cannot_use_missing_or_sibling_bank_lineage(
    bank_integrity_schema: MigratedPostgresSchema,
    lineage: str,
) -> None:
    with (
        bank_integrity_schema.app_engine.connect() as connection,
        connection.begin() as transaction,
    ):
        sibling_banks_scenario(connection)
        row = canonical_row(connection, "canonical_loan_events", BANK)
        row["lineage_id"] = (
            uuid4()
            if lineage == "missing"
            else insert_row(
                connection,
                "lineage_records",
                canonical_row(connection, "lineage_records", SIBLING_BANK),
            )
        )
        with pytest.raises(IntegrityError) as failure:
            insert_row(connection, "canonical_loan_events", row)
        database_error = failure.value.orig
        assert isinstance(database_error, ForeignKeyViolation)
        assert database_error.diag.constraint_name == "fk_canonical_loan_events_lineage_id_bank"
        transaction.rollback()


@pytest.mark.parametrize("lineage", ["missing", "sibling_bank"])
def test_existing_invalid_loan_lineage_stops_the_migration(
    bank_integrity_schema: MigratedPostgresSchema,
    lineage: str,
) -> None:
    revision = ScriptDirectory.from_config(alembic_config_for_app()).get_revision("202610110095")
    assert revision is not None
    migration = cast(CanonicalBankMigration, revision.module)
    with (
        bank_integrity_schema.app_engine.connect() as connection,
        connection.begin() as transaction,
    ):
        sibling_banks_scenario(connection)
        row = canonical_row(connection, "canonical_loan_events", BANK)
        row["lineage_id"] = (
            uuid4()
            if lineage == "missing"
            else insert_row(
                connection,
                "lineage_records",
                canonical_row(connection, "lineage_records", SIBLING_BANK),
            )
        )
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
            insert_row(connection, "canonical_loan_events", row)
            connection.execute(text("SELECT set_config('app.organization_id', '', true)"))
            with pytest.raises(RuntimeError, match="canonical_loan_events.lineage_id: 1 invalid"):
                migration.upgrade()
        transaction.rollback()
