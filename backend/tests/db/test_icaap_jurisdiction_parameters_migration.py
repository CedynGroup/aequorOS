"""Postgres proof for ``202609200063`` (the Nigerian and Kenyan ICAAP rows).

Worth a real database rather than the hermetic suite for one reason the other
seed revisions did not have: **every code here already governs Ghana.** The
"is it already governed" probe and the downgrade therefore have to key on
(jurisdiction, code) rather than on the code, and both failure modes are
silent:

* a code-only probe finds Ghana's row, decides the code is governed, and seeds
  nothing at all — the frameworks stay broken and the migration reports success;
* a code-only DELETE on downgrade takes Ghana's eight workspace rows and two
  Pillar 2 rows with it, and the ICAAP workspace stops resolving for the one
  jurisdiction that was working.

Also pinned: the structural bodies. The materiality bands are JSON, and a body
that was inserted as text or that lost a nested integer on the way through the
column looks correct in Python and wrong in Postgres.
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text

from alembic import command
from app.services.icaap.parameters import ICAAP_JURISDICTION_SEED_PARAMETERS
from tests.db.test_postgres_migrations import (
    MigratedPostgresSchema,
    alembic_config_for_app,
    migrated_postgres_schema,
)

__all__ = ["migrated_postgres_schema"]

pytestmark = pytest.mark.committing_db

PREVIOUS_REVISION = "202609190062"
REVISION = "202609200063"
JURISDICTIONS = ("NG", "KE")
EXPECTED = {
    (spec.jurisdiction_code, spec.param_code): (
        None if spec.value is None else Decimal(spec.value),
        None if spec.value_json is None else dict(spec.value_json),
        spec.unit,
        spec.confirmation_status,
        spec.source_citation,
    )
    for spec in ICAAP_JURISDICTION_SEED_PARAMETERS
}
CODES = tuple(sorted({spec.param_code for spec in ICAAP_JURISDICTION_SEED_PARAMETERS}))

requires_postgres = pytest.mark.skipif(
    os.getenv("TEST_DATABASE_URL") is None,
    reason="TEST_DATABASE_URL is required for Postgres migration tests.",
)


def _rows(schema: MigratedPostgresSchema, jurisdictions: tuple[str, ...]) -> list[dict[str, Any]]:
    with schema.app_engine.begin() as connection:
        rows = connection.execute(
            text(
                """
                SELECT param_code, scope_type, scope_key, jurisdiction_code, value_numeric,
                       value_json, unit, source_citation, confirmation_status, status,
                       proposed_by, approved_by, effective_from, effective_to
                FROM regulatory_parameter
                WHERE param_code = ANY(:codes) AND jurisdiction_code = ANY(:jurisdictions)
                ORDER BY jurisdiction_code, param_code, effective_from
                """
            ),
            {"codes": list(CODES), "jurisdictions": list(jurisdictions)},
        ).mappings()
        return [dict(row) for row in rows]


def _assert_seeded(rows: list[dict[str, Any]]) -> None:
    seeded = [row for row in rows if row["proposed_by"] == "platform_seed"]
    assert sorted((r["jurisdiction_code"], r["param_code"]) for r in seeded) == sorted(EXPECTED)
    for row in seeded:
        key = (row["jurisdiction_code"], row["param_code"])
        value, body, unit, confirmation, citation = EXPECTED[key]
        assert (row["scope_type"], row["scope_key"]) == ("institution_class", "bank")
        assert row["value_numeric"] == value, key
        assert row["value_json"] == body, key
        assert (row["unit"], row["confirmation_status"]) == (unit, confirmation), key
        assert row["source_citation"] == citation, key
        assert (row["status"], row["effective_to"]) == ("approved", None)
        # Neither primary text has been read here (D-076); nothing may present
        # as confirmed, however plainly the paragraph is quoted.
        assert row["confirmation_status"] == "pending", key


@requires_postgres
def test_the_seed_lands_and_the_downgrade_leaves_ghana_alone(
    migrated_postgres_schema: MigratedPostgresSchema,
) -> None:
    config = alembic_config_for_app()

    _assert_seeded(_rows(migrated_postgres_schema, JURISDICTIONS))
    ghana_before = _rows(migrated_postgres_schema, ("GH",))
    # Every one of these codes governs Ghana too — that is the whole hazard.
    assert {row["param_code"] for row in ghana_before} == set(CODES)

    command.downgrade(config, PREVIOUS_REVISION)
    assert _rows(migrated_postgres_schema, JURISDICTIONS) == []
    assert _rows(migrated_postgres_schema, ("GH",)) == ghana_before

    command.upgrade(config, REVISION)
    _assert_seeded(_rows(migrated_postgres_schema, JURISDICTIONS))


@requires_postgres
def test_an_operator_generation_is_left_alone_and_never_duplicated(
    migrated_postgres_schema: MigratedPostgresSchema,
) -> None:
    """D-053, with the jurisdiction in the key.

    An operator who has already corrected Nigeria's deadline in the console
    must keep that row. Re-running the revision must not insert a second
    generation beside it — which the collision rule would date a day earlier,
    making it the ACTIVE value of a figure nobody proposed.
    """
    config = alembic_config_for_app()
    command.downgrade(config, PREVIOUS_REVISION)

    now = datetime.now(UTC)
    with migrated_postgres_schema.app_engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO regulatory_parameter (
                    id, scope_type, scope_key, param_code, jurisdiction_code, value_numeric,
                    value_json, unit, source_citation, confirmation_status, effective_from,
                    effective_to, status, proposed_by, approved_by, approved_at,
                    created_at, updated_at
                ) VALUES (
                    :id, 'institution_class', 'bank', 'icaap_submission_months', 'NG', 5,
                    NULL, 'months', 'Operator correction after reading the CBN Guidelines',
                    'confirmed', :effective_from, NULL, 'approved', 'operator', 'checker',
                    :now, :now, :now
                )
                """
            ),
            {"id": uuid4(), "effective_from": date(2020, 1, 1), "now": now},
        )

    command.upgrade(config, REVISION)

    rows = [
        row
        for row in _rows(migrated_postgres_schema, ("NG",))
        if row["param_code"] == "icaap_submission_months"
    ]
    assert len(rows) == 1, "the seed must not add a second generation beside an approved row"
    assert rows[0]["proposed_by"] == "operator"
    assert rows[0]["value_numeric"] == Decimal(5)
    # The rest of Nigeria still seeded: the guard is per row, not per revision.
    other = {
        row["param_code"]
        for row in _rows(migrated_postgres_schema, ("NG",))
        if row["proposed_by"] == "platform_seed"
    }
    assert "icaap_materiality_rating_bands" in other
    assert "icaap_submission_months" not in other
