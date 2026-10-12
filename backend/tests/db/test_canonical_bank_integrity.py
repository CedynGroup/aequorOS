"""Every canonical parent reference must belong to the child's institution."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from typing import Protocol, cast
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from psycopg.errors import CheckViolation, ForeignKeyViolation
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from app.db.base import Base
from tests.db.test_bank_organization_integrity import (
    BANK,
    ORG,
    OTHER_BANK,
    bank_scope_scenario,
    pytestmark,
)
from tests.db.test_postgres_migrations import (
    MigratedPostgresSchema,
    alembic_config_for_app,
    migrate_isolated_postgres_schema,
)

__all__ = ["bank_integrity_schema", "pytestmark"]
SIBLING_BANK = "BK-INTEGRITY-A2"
CANONICAL_TABLES = tuple(
    name
    for name in Base.metadata.tables
    if name.startswith("canonical_") and "lineage_id" in Base.metadata.tables[name].columns
)
REFERENCES = tuple(
    sorted(
        (name, fk.elements[0].parent.name, fk.referred_table.name)
        for name in (*CANONICAL_TABLES, "lineage_records")
        for fk in Base.metadata.tables[name].foreign_key_constraints
        if len(fk.elements) == 3
        and fk.elements[0].parent.name not in ("superseded_by", "withdrawn_by_batch_id")
        # The independently shipped next layer repairs this pre-existing omission.
        and (name, fk.elements[0].parent.name) != ("canonical_loan_events", "lineage_id")
    )
)
AS_OF = date(2026, 6, 30)


@pytest.fixture(scope="module")
def bank_integrity_schema() -> Iterator[MigratedPostgresSchema]:
    with pytest.MonkeyPatch.context() as monkeypatch:
        yield from migrate_isolated_postgres_schema(
            monkeypatch, downgrade=False, revision="202610110094"
        )


class CanonicalBankMigration(Protocol):
    def upgrade(self) -> None: ...
    def downgrade(self) -> None: ...


def sibling_banks_scenario(connection: Connection) -> None:
    bank_scope_scenario(connection)
    connection.execute(
        Base.metadata.tables["banks"]
        .insert()
        .values(
            id=SIBLING_BANK,
            organization_id=ORG,
            name="Sibling bank",
            short_name="Sibling",
            currency="GHS",
            jurisdiction_code="GH",
            license_type="bank",
            institution_type="universal_bank",
        )
    )


def insert_row(connection: Connection, table: str, row: dict[str, object]) -> UUID:
    mapped = Base.metadata.tables[table]
    return cast(
        UUID, connection.execute(mapped.insert().values(**row).returning(mapped.c.id)).scalar_one()
    )


def canonical_row(
    connection: Connection, table: str, bank: str, organization: str = ORG
) -> dict[str, object]:
    """An otherwise valid canonical record and its own-bank prerequisites."""
    if table == "ingestion_batches":
        return dict(
            organization_id=organization,
            bank_id=bank,
            source_system="MANUAL",
            adapter_version="proof-v1",
            extraction_mode="full",
            status="accepted",
            as_of_date=AS_OF,
        )
    batch = insert_row(
        connection,
        "ingestion_batches",
        canonical_row(connection, "ingestion_batches", bank, organization),
    )
    node: dict[str, object] = dict(
        organization_id=organization,
        bank_id=bank,
        ingestion_batch_id=batch,
        operation_type="VALIDATION",
        operation_ref="integrity-proof",
    )
    if table == "lineage_records":
        return node
    lineage = insert_row(connection, "lineage_records", node)
    row: dict[str, object] = dict(
        organization_id=organization,
        bank_id=bank,
        ingestion_batch_id=batch,
        lineage_id=lineage,
        as_of_date=AS_OF,
        source_system="MANUAL",
        source_reference=str(uuid4()),
    )
    row.update(
        {
            "canonical_gl_accounts": dict(
                account_code=str(uuid4()), name="Proof", account_class="ASSET"
            ),
            "canonical_counterparties": dict(name="Proof", counterparty_type="CORPORATE"),
            "canonical_products": dict(product_code=str(uuid4()), name="Proof"),
            "canonical_positions": dict(position_type="LOAN", currency="GHS"),
            "canonical_position_snapshots": dict(balance=1),
            "canonical_reference_rows": dict(
                dataset_kind="capital_structure", row_index=1, payload={}
            ),
            "canonical_yield_curves": dict(
                currency="GHS", curve_name=str(uuid4()), curve_type="sovereign"
            ),
            "canonical_yield_curve_points": dict(tenor_months=1, rate="0.01"),
            "canonical_fx_rates": dict(
                base_currency="GHS", quote_currency="USD", rate_type="spot", rate=1
            ),
            "canonical_market_indices": dict(index_code=str(uuid4()), value=1),
            "canonical_counterparty_ratings": dict(
                issuer=str(uuid4()), agency="moodys", rating="A", rating_date=AS_OF
            ),
            "canonical_loan_events": dict(
                event_type="REPAYMENT",
                event_date=AS_OF,
                position_source_reference="proof",
                amount=1,
                currency="GHS",
            ),
        }[table]
    )
    if table == "canonical_reference_rows":
        row.pop("source_system")
    if table == "canonical_position_snapshots":
        row["position_id"] = insert_row(
            connection,
            "canonical_positions",
            canonical_row(connection, "canonical_positions", bank, organization),
        )
    if table == "canonical_yield_curve_points":
        row["yield_curve_id"] = insert_row(
            connection,
            "canonical_yield_curves",
            canonical_row(connection, "canonical_yield_curves", bank, organization),
        )
    return row


@pytest.mark.parametrize(("table", "column", "parent"), REFERENCES)
def test_a_parent_from_a_sibling_bank_is_refused(
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
        row = canonical_row(connection, table, BANK)
        row[column] = insert_row(
            connection, parent, canonical_row(connection, parent, SIBLING_BANK)
        )
        with pytest.raises(IntegrityError) as failure:
            connection.execute(Base.metadata.tables[table].insert().values(**row))
        database_error = failure.value.orig
        assert isinstance(database_error, ForeignKeyViolation)
        assert database_error.diag.constraint_name == f"fk_{table}_{column}_bank"
        transaction.rollback()


@pytest.mark.parametrize("table", (*CANONICAL_TABLES, "lineage_records"))
def test_own_bank_canonical_provenance_is_admitted(
    bank_integrity_schema: MigratedPostgresSchema,
    table: str,
) -> None:
    with (
        bank_integrity_schema.app_engine.connect() as connection,
        connection.begin() as transaction,
    ):
        sibling_banks_scenario(connection)
        result = insert_row(connection, table, canonical_row(connection, table, BANK))
        assert isinstance(result, UUID)
        transaction.rollback()


def test_the_new_bank_bound_foreign_keys_match_the_migrated_schema(
    bank_integrity_schema: MigratedPostgresSchema,
) -> None:
    inspector = sa.inspect(bank_integrity_schema.app_engine)
    for table, column, parent in REFERENCES:
        assert any(
            fk["constrained_columns"] == [column, "organization_id", "bank_id"]
            and fk["referred_table"] == parent
            and fk["referred_columns"] == ["id", "organization_id", "bank_id"]
            for fk in inspector.get_foreign_keys(table, schema=bank_integrity_schema.schema_name)
        ), (table, column)
    referenced_parents = {parent for _, _, parent in REFERENCES}
    for name in (*CANONICAL_TABLES, "lineage_records", "ingestion_batches"):
        assert any(
            uq["column_names"] == ["id", "organization_id", "bank_id"]
            for uq in inspector.get_unique_constraints(
                name, schema=bank_integrity_schema.schema_name
            )
        ) == (name in referenced_parents), name


@pytest.mark.parametrize(
    ("table", "column", "parent"),
    [reference for reference in REFERENCES if reference[0] != "lineage_records"],
)
def test_existing_cross_bank_references_fail_migration_verification(
    bank_integrity_schema: MigratedPostgresSchema,
    table: str,
    column: str,
    parent: str,
) -> None:
    revision = ScriptDirectory.from_config(alembic_config_for_app()).get_revision("202610110094")
    assert revision is not None
    migration = cast(CanonicalBankMigration, revision.module)
    with (
        bank_integrity_schema.app_engine.connect() as connection,
        connection.begin() as transaction,
    ):
        sibling_banks_scenario(connection)
        row = canonical_row(connection, table, BANK)
        other = insert_row(connection, parent, canonical_row(connection, parent, SIBLING_BANK))
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
            row[column] = other
            connection.execute(
                sa.text("SELECT set_config('app.organization_id', :org, true)"), {"org": ORG}
            )
            connection.execute(Base.metadata.tables[table].insert().values(**row))
            connection.execute(sa.text("SELECT set_config('app.organization_id', '', true)"))
            with pytest.raises(RuntimeError, match=f"{table}.{column}: 1 references violate"):
                migration.upgrade()
        transaction.rollback()


def test_batch_bound_lineage_cannot_omit_its_bank(
    bank_integrity_schema: MigratedPostgresSchema,
) -> None:
    with (
        bank_integrity_schema.app_engine.connect() as connection,
        connection.begin() as transaction,
    ):
        sibling_banks_scenario(connection)
        row = canonical_row(connection, "lineage_records", BANK)
        row["bank_id"] = None
        with pytest.raises(IntegrityError) as failure:
            connection.execute(Base.metadata.tables["lineage_records"].insert().values(**row))
        database_error = failure.value.orig
        assert isinstance(database_error, CheckViolation)
        assert database_error.diag.constraint_name == "ck_lineage_records_batch_bank"
        transaction.rollback()


def test_batchless_lineage_may_remain_organization_wide(
    bank_integrity_schema: MigratedPostgresSchema,
) -> None:
    with (
        bank_integrity_schema.app_engine.connect() as connection,
        connection.begin() as transaction,
    ):
        sibling_banks_scenario(connection)
        node = insert_row(
            connection,
            "lineage_records",
            dict(
                organization_id=ORG,
                operation_type="VALIDATION",
                operation_ref="global graph node",
            ),
        )
        assert isinstance(node, UUID)
        transaction.rollback()


def test_lineage_cannot_claim_a_bank_owned_by_another_organization(
    bank_integrity_schema: MigratedPostgresSchema,
) -> None:
    with (
        bank_integrity_schema.app_engine.connect() as connection,
        connection.begin() as transaction,
    ):
        sibling_banks_scenario(connection)
        with pytest.raises(IntegrityError) as failure:
            insert_row(
                connection,
                "lineage_records",
                dict(
                    organization_id=ORG,
                    bank_id=OTHER_BANK,
                    operation_type="VALIDATION",
                    operation_ref="scope proof",
                ),
            )
        database_error = failure.value.orig
        assert isinstance(database_error, ForeignKeyViolation)
        assert database_error.diag.constraint_name == "fk_lineage_records_bank"
        transaction.rollback()


def test_existing_lineage_bank_ownership_is_backfilled_from_the_batch(
    bank_integrity_schema: MigratedPostgresSchema,
) -> None:
    revision = ScriptDirectory.from_config(alembic_config_for_app()).get_revision("202610110094")
    assert revision is not None
    migration = cast(CanonicalBankMigration, revision.module)
    with (
        bank_integrity_schema.app_engine.connect() as connection,
        connection.begin() as transaction,
    ):
        sibling_banks_scenario(connection)
        node = insert_row(
            connection, "lineage_records", canonical_row(connection, "lineage_records", BANK)
        )
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
            migration.upgrade()
        stored_bank = cast(
            str,
            connection.scalar(
                sa.select(Base.metadata.tables["lineage_records"].c.bank_id).where(
                    Base.metadata.tables["lineage_records"].c.id == node
                )
            ),
        )
        assert stored_bank == BANK
        transaction.rollback()
