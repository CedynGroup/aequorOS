"""Postgres proof for the ``performance_targets`` dataset kind (``202609220068``).

Two things are proven on a MIGRATED database, because neither can be seen on the
hermetic ``create_all`` schema:

* **parity** between ``REFERENCE_DATASET_KINDS`` and the migrated CHECK. The
  model derives ``ck_canonical_reference_rows_dataset_kind`` from that tuple
  while the migration writes a literal list, so a fresh database always admits a
  newly added kind and a migrated one may not — the disagreement that produced a
  live 500 for the role-bundle constraint (``202609200065``). A kind added to the
  tuple without a widening migration fails here instead of in a bank's push;
* the **before / after** behaviour of the widening itself: a
  ``performance_targets`` row is refused by the database at ``202609220067`` and
  accepted at ``202609220068``, while ``business_units`` — already an admitted
  kind, deliberately NOT re-added by the migration — is accepted at both. The
  downgrade removes the target rows (they cannot satisfy the narrowed
  constraint) and leaves the register alone.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, date, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from alembic import command
from app.domain.ingestion.constants import REFERENCE_DATASET_KINDS
from tests.db.test_initial_owner_migration import _insert_organization
from tests.db.test_postgres_migrations import (
    MigratedPostgresSchema,
    alembic_config_for_app,
    clear_database_caches,
    migrated_postgres_schema,
)

__all__ = ["migrated_postgres_schema"]

pytestmark = [
    pytest.mark.committing_db,
    pytest.mark.skipif(
        os.getenv("TEST_DATABASE_URL") is None,
        reason="TEST_DATABASE_URL is required for Postgres migration tests.",
    ),
]

_CONSTRAINT = "ck_canonical_reference_rows_dataset_kind"
REVISION = "202609220068"
PREVIOUS_REVISION = "202609220067"
NEW_KIND = "performance_targets"
ORG = "OR-TARG0001"
BANK = "BK-TARG0001"
AS_OF = date(2026, 3, 31)

_TARGET_PAYLOAD = {
    "period": "2026-03-31",
    "grain": "quarter",
    "measure_id": "loans.balance_rc",
    "time_behaviour": "stock",
    "value": "1250000.00",
    "version": "budget",
}
_UNIT_PAYLOAD = {"business_unit_id": "BR-001", "business_unit_name": "Accra Main"}


def _set_tenant(connection: Connection, organization_id: str) -> None:
    connection.execute(
        text("SELECT set_config('app.organization_id', :organization_id, true)"),
        {"organization_id": organization_id},
    )


def _insert_bank(connection: Connection, *, now: datetime) -> None:
    connection.execute(
        text(
            """
            INSERT INTO banks
              (id, organization_id, name, short_name, currency, jurisdiction_code,
               license_type, institution_type, created_at, updated_at)
            VALUES
              (:bank_id, :organization_id, 'Targets proof bank', 'TRP', 'GHS', 'GH',
               'universal', 'universal_bank', :now, :now)
            """
        ),
        {"bank_id": BANK, "organization_id": ORG, "now": now},
    )


def _insert_batch(connection: Connection, *, now: datetime) -> tuple[UUID, UUID]:
    """One accepted batch and its validation lineage node — the two rows a
    reference row must point at before the dataset kind gets a say."""
    batch_id, lineage_id = uuid4(), uuid4()
    connection.execute(
        text(
            """
            INSERT INTO ingestion_batches
              (id, organization_id, bank_id, source_system, adapter_version, extraction_mode,
               status, as_of_date, created_at, updated_at)
            VALUES
              (:batch_id, :organization_id, :bank_id, 'EXCEL_CSV', 'test', 'full',
               'accepted', :as_of, :now, :now)
            """
        ),
        {
            "batch_id": batch_id,
            "organization_id": ORG,
            "bank_id": BANK,
            "as_of": AS_OF,
            "now": now,
        },
    )
    connection.execute(
        text(
            """
            INSERT INTO lineage_records
              (id, organization_id, ingestion_batch_id, operation_type, operation_ref, occurred_at)
            VALUES
              (:lineage_id, :organization_id, :batch_id, 'VALIDATION', 'targets-proof', :now)
            """
        ),
        {
            "lineage_id": lineage_id,
            "organization_id": ORG,
            "batch_id": batch_id,
            "now": now,
        },
    )
    return batch_id, lineage_id


def _insert_reference_row(  # noqa: PLR0913 - the row's lineage keys are all explicit
    connection: Connection,
    *,
    batch_id: UUID,
    lineage_id: UUID,
    kind: str,
    row_index: int,
    payload: dict[str, str],
    now: datetime,
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO canonical_reference_rows
              (id, organization_id, bank_id, ingestion_batch_id, as_of_date, dataset_kind,
               row_index, payload, source_reference, lineage_id, created_at, updated_at)
            VALUES
              (:id, :organization_id, :bank_id, :batch_id, :as_of, :kind, :row_index,
               CAST(:payload AS json), :source_reference, :lineage_id, :now, :now)
            """
        ),
        {
            "id": uuid4(),
            "organization_id": ORG,
            "bank_id": BANK,
            "batch_id": batch_id,
            "as_of": AS_OF,
            "kind": kind,
            "row_index": row_index,
            "payload": json.dumps(payload),
            "source_reference": f"targets-proof#{kind}!{row_index}",
            "lineage_id": lineage_id,
            "now": now,
        },
    )


def _constraint_definition(schema: MigratedPostgresSchema) -> str:
    with schema.app_engine.connect() as connection:
        # Scoped to THIS migrated schema: the disposable test schemas are created
        # alongside each other, so the constraint name alone is not unique.
        return connection.execute(
            text(
                "SELECT pg_get_constraintdef(c.oid) FROM pg_constraint c "
                "JOIN pg_class t ON t.oid = c.conrelid "
                "JOIN pg_namespace n ON n.oid = t.relnamespace "
                "WHERE c.conname = :name AND n.nspname = :schema"
            ),
            {"name": _CONSTRAINT, "schema": schema.schema_name},
        ).scalar_one()


def _kind_counts(schema: MigratedPostgresSchema) -> dict[str, int]:
    with schema.app_engine.begin() as connection:
        _set_tenant(connection, ORG)
        return {
            str(kind): int(count)
            for kind, count in connection.execute(
                text(
                    "SELECT dataset_kind, count(*) FROM canonical_reference_rows "
                    "WHERE organization_id = :organization_id GROUP BY dataset_kind"
                ),
                {"organization_id": ORG},
            ).tuples()
        }


def test_the_dataset_kind_constraint_accepts_every_reference_dataset_kind(
    migrated_postgres_schema: MigratedPostgresSchema,
) -> None:
    definition = _constraint_definition(migrated_postgres_schema)

    missing = sorted(kind for kind in REFERENCE_DATASET_KINDS if f"'{kind}'" not in definition)
    assert not missing, (
        f"{_CONSTRAINT} rejects {missing}. A new reference dataset kind needs a "
        "migration widening this constraint — the model derives it from "
        "REFERENCE_DATASET_KINDS, so the hermetic suite will not catch this."
    )
    # Asserted by literal as well, so the proof survives a refactor of the tuple.
    assert f"'{NEW_KIND}'" in definition


def test_target_rows_are_refused_before_the_widening_and_accepted_after(
    migrated_postgres_schema: MigratedPostgresSchema,
) -> None:
    config = alembic_config_for_app()
    command.downgrade(config, PREVIOUS_REVISION)
    clear_database_caches()
    now = datetime.now(UTC)

    with migrated_postgres_schema.app_engine.begin() as connection:
        _insert_organization(connection, ORG, now)
        _insert_bank(connection, now=now)
        batch_id, lineage_id = _insert_batch(connection, now=now)
        # `business_units` has been admitted since the constraint was written,
        # which is why 202609220068 must not re-add it.
        _insert_reference_row(
            connection,
            batch_id=batch_id,
            lineage_id=lineage_id,
            kind="business_units",
            row_index=1,
            payload=_UNIT_PAYLOAD,
            now=now,
        )

    with (
        pytest.raises(IntegrityError) as refused,
        migrated_postgres_schema.app_engine.begin() as connection,
    ):
        _set_tenant(connection, ORG)
        _insert_reference_row(
            connection,
            batch_id=batch_id,
            lineage_id=lineage_id,
            kind=NEW_KIND,
            row_index=1,
            payload=_TARGET_PAYLOAD,
            now=now,
        )
    assert _CONSTRAINT in str(refused.value)
    assert f"'{NEW_KIND}'" not in _constraint_definition(migrated_postgres_schema)

    command.upgrade(config, REVISION)
    clear_database_caches()

    with migrated_postgres_schema.app_engine.begin() as connection:
        _set_tenant(connection, ORG)
        _insert_reference_row(
            connection,
            batch_id=batch_id,
            lineage_id=lineage_id,
            kind=NEW_KIND,
            row_index=1,
            payload=_TARGET_PAYLOAD,
            now=now,
        )
    assert _kind_counts(migrated_postgres_schema) == {"business_units": 1, NEW_KIND: 1}

    command.downgrade(config, PREVIOUS_REVISION)
    clear_database_caches()

    # The narrowed constraint cannot hold a target row, so the downgrade deletes
    # them — and only them.
    assert _kind_counts(migrated_postgres_schema) == {"business_units": 1}
    assert f"'{NEW_KIND}'" not in _constraint_definition(migrated_postgres_schema)

    command.upgrade(config, "head")
    clear_database_caches()

    # Clear only the rows this test added, child-first, and only in tables the
    # app role may write (audit A7-03).
    #
    # ``banks`` and ``organizations`` are deliberately NOT deleted. Deleting a
    # bank makes Postgres take a ``FOR KEY SHARE`` lock on every referencing
    # table to enforce the foreign keys, and the app role holds no privilege on
    # some of them (``regulatory_package_attachments`` among others), so the
    # statement raises InsufficientPrivilege as the NOSUPERUSER/NOBYPASSRLS role
    # CI runs as. The aborted transaction then left the seeded rows behind,
    # which is what actually broke the fixture's walk to base.
    #
    # No other migration test cleans up at all: ``downgrade base`` DROPS the
    # tables, so leftover rows are harmless. The one thing that is not harmless
    # is a row that violates a CHECK an earlier migration re-asserts on the way
    # down — which is why the batch above is seeded ``EXCEL_CSV`` and not a value
    # added to ``ck_ingestion_batches_source_system`` later in the chain.
    with migrated_postgres_schema.app_engine.begin() as connection:
        _set_tenant(connection, ORG)
        for table in ("canonical_reference_rows", "lineage_records", "ingestion_batches"):
            connection.execute(
                text(f"DELETE FROM {table} WHERE organization_id = :organization_id"),
                {"organization_id": ORG},
            )
