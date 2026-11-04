"""Independent Postgres QA of migration 202609190054 (ICAAP P0, B2).

Agent 11 (Test/QA), 2026-09-19. On a throwaway ``TEST_DATABASE_URL`` schema:
upgrade -> downgrade -> upgrade round trip; the downgrade removes exactly the
eight seeded rows and leaves every other control-plane row byte-for-byte; a
re-run of the upgrade step over already-seeded data inserts nothing.
"""

from __future__ import annotations

import os
from decimal import Decimal

import pytest
from sqlalchemy import text

from alembic import command
from tests.db.test_postgres_migrations import (
    MigratedPostgresSchema,
    alembic_config_for_app,
    migrated_postgres_schema,
)

__all__ = ["migrated_postgres_schema"]

pytestmark = pytest.mark.committing_db

SDI_CAPS = {"at1_cap_pct_rwa": Decimal("1.5"), "tier2_cap_pct_rwa": Decimal("2")}
PREVIOUS = "202609160053"
REVISION = "202609190054"
B2 = {
    "cet1_min": Decimal("6.5"),
    "tier1_min": Decimal("8"),
    "leverage_min": Decimal("6"),
    "ccb1_pct": Decimal("3"),
    "ccyb_pct": Decimal("0"),
    "dsib_buffer_pct": Decimal("0"),
    "at1_cap_pct_rwa": Decimal("1.5"),
    "tier2_cap_pct_rwa": Decimal("2"),
}


def _snapshot(schema: MigratedPostgresSchema) -> set[tuple[object, ...]]:
    with schema.app_engine.connect() as connection:
        return {
            tuple(row)
            for row in connection.execute(
                text(
                    """
                    SELECT id, scope_type, scope_key, param_code, jurisdiction_code,
                           value_numeric, unit, source_citation, confirmation_status,
                           effective_from, effective_to, status, proposed_by, approved_by
                    FROM regulatory_parameter
                    """
                )
            )
        }


def _version(schema: MigratedPostgresSchema) -> str:
    with schema.app_engine.connect() as connection:
        return str(connection.scalar(text("SELECT version_num FROM alembic_version")))


@pytest.mark.skipif(
    os.getenv("TEST_DATABASE_URL") is None,
    reason="TEST_DATABASE_URL is required for Postgres migration tests.",
)
def test_round_trip_is_exact_and_upgrade_is_idempotent(
    migrated_postgres_schema: MigratedPostgresSchema,
) -> None:
    config = alembic_config_for_app()
    # Later ICAAP migrations seed the same control plane; the round trip is
    # measured from this revision, so its downgrade removes only its own rows.
    command.downgrade(config, REVISION)
    assert _version(migrated_postgres_schema) == REVISION

    at_head = _snapshot(migrated_postgres_schema)
    seeded = {row for row in at_head if row[3] in B2}
    # D-042: the SDI class carries the two recognition caps (pre-2026-09-19 values).
    sdi = {row for row in seeded if row[2] == "sdi"}
    assert {(str(row[3]), row[5]) for row in sdi} == set(SDI_CAPS.items())
    assert {row[8] for row in sdi} == {"pending"}
    bank = seeded - sdi
    assert len(bank) == len(B2)
    for row in bank:
        assert (row[1], row[2], row[4]) == ("institution_class", "bank", "GH")
        assert row[5] == B2[str(row[3])]
        assert (row[11], row[12]) == ("approved", "platform_seed")

    command.downgrade(config, PREVIOUS)
    assert _version(migrated_postgres_schema) == PREVIOUS
    assert _snapshot(migrated_postgres_schema) == at_head - seeded

    command.upgrade(config, REVISION)
    again = _snapshot(migrated_postgres_schema)
    assert {row[1:] for row in again} == {row[1:] for row in at_head}  # ids are fresh
    assert len(again) == len(at_head)

    # Re-run the data step over already-seeded rows: nothing is inserted.
    command.stamp(config, PREVIOUS)
    command.upgrade(config, REVISION)
    assert _snapshot(migrated_postgres_schema) == again
