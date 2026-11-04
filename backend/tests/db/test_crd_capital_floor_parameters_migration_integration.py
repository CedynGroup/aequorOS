"""Postgres proof for the CRD capital-floor control-plane seed (202609190054).

Regulatory audit B2 / M21 (ICAAP P0): ``cet1_min``, ``tier1_min``,
``leverage_min`` (6, fixing the 3% register default), the CCB1 / CCyB / D-SIB
buffers and the AT1 / Tier 2 recognition caps become governed bank-class rows.
The migration must seed exactly the catalogue values, be idempotent against a
row already present for the same scope (an operator-approved generation is
never duplicated or overwritten), and its downgrade must remove only the rows
the seed itself wrote.

This module's other-suite cases live in

``test_crd_capital_floor_parameters_migration.py``.
"""

from __future__ import annotations

from tests.db.test_crd_capital_floor_parameters_migration import (
    CODES,
    EXPECTED,
    PENDING,
    SDI_EXPECTED,
)

__all__ = ["migrated_postgres_schema"]
import os
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import text

from alembic import command
from app.policy.service import regulatory_parameters
from tests.db.test_postgres_migrations_integration import (
    MigratedPostgresSchema,
    alembic_config_for_app,
    migrated_postgres_schema,
)

pytestmark = pytest.mark.committing_db
PREVIOUS_REVISION = "202609160053"
REVISION = "202609190054"


def _rows(schema: MigratedPostgresSchema) -> list[dict]:
    with schema.app_engine.begin() as connection:
        rows = connection.execute(
            text(
                """
                SELECT param_code, scope_type, scope_key, jurisdiction_code, value_numeric,
                       unit, source_citation, confirmation_status, status, proposed_by,
                       effective_from
                FROM regulatory_parameter
                WHERE param_code = ANY(:codes)
                ORDER BY param_code, scope_type, scope_key, effective_from
                """
            ),
            {"codes": list(CODES)},
        ).mappings()
        return [dict(row) for row in rows]


def _assert_seeded(rows: list[dict]) -> None:
    seeded_all = [row for row in rows if row["proposed_by"] == "platform_seed"]
    sdi = [row for row in seeded_all if row["scope_key"] == "sdi"]
    seeded = [row for row in seeded_all if row["scope_key"] != "sdi"]
    assert sorted(row["param_code"] for row in seeded) == sorted(CODES)
    assert {row["param_code"]: row["value_numeric"] for row in sdi} == SDI_EXPECTED
    for row in sdi:
        assert row["scope_type"] == "institution_class"
        assert (row["status"], row["confirmation_status"]) == ("approved", "pending")
        assert "no SDI-specific regulatory basis identified" in row["source_citation"]
    for row in seeded:
        assert (row["scope_type"], row["scope_key"]) == ("institution_class", "bank")
        assert row["value_numeric"] == EXPECTED[row["param_code"]]
        assert row["unit"] == "percent"
        assert row["status"] == "approved"
        expected_status = "pending" if row["param_code"] in PENDING else "confirmed"
        assert row["confirmation_status"] == expected_status, row["param_code"]
        assert "Capital Requirements Directive 2018" in row["source_citation"]
        assert ("pending stakeholder confirmation" in row["source_citation"]) is (
            row["param_code"] in PENDING
        )
        assert "M20" not in row["source_citation"]  # D-042: no internal reference printed
    # The SDI class gets only the two caps (s.29 has no Basel sub-tier floor).
    assert {row["param_code"] for row in rows if row["scope_key"] == "sdi"} <= set(SDI_EXPECTED)


@pytest.mark.skipif(
    os.getenv("TEST_DATABASE_URL") is None,
    reason="TEST_DATABASE_URL is required for Postgres migration tests.",
)
def test_seed_is_idempotent_and_its_downgrade_removes_only_its_own_rows(
    migrated_postgres_schema: MigratedPostgresSchema,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = alembic_config_for_app()

    # At head: every code present exactly once for the bank class.
    _assert_seeded(_rows(migrated_postgres_schema))

    # Downgrade removes the seeded rows.
    command.downgrade(config, PREVIOUS_REVISION)
    assert _rows(migrated_postgres_schema) == []

    # An operator-approved leverage generation exists before the upgrade runs:
    # it must survive, and the seed must not add a second bank leverage row.
    now = datetime.now(UTC)
    with migrated_postgres_schema.app_engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO regulatory_parameter
                    (id, scope_type, scope_key, param_code, jurisdiction_code,
                     value_numeric, value_json, unit, source_citation,
                     confirmation_status, effective_from, effective_to, status,
                     proposed_by, approved_by, approved_at, change_rationale,
                     created_at, updated_at)
                VALUES
                    (:id, 'institution_class', 'bank', 'leverage_min', 'GH',
                     7, NULL, 'percent', 'Operator-approved generation (test)',
                     'confirmed', :effective_from, NULL, 'approved',
                     'operator:maker', 'operator:checker', :now, 'test',
                     :now, :now)
                """
            ),
            {"id": uuid4(), "effective_from": date(2024, 1, 1), "now": now},
        )

    command.upgrade(config, REVISION)
    rows = _rows(migrated_postgres_schema)
    leverage = [row for row in rows if row["param_code"] == "leverage_min"]
    assert [row["proposed_by"] for row in leverage] == ["operator:maker"]
    assert leverage[0]["value_numeric"] == Decimal("7")
    seeded_codes = sorted(
        row["param_code"]
        for row in rows
        if row["proposed_by"] == "platform_seed" and row["scope_key"] == "bank"
    )
    assert seeded_codes == sorted(code for code in CODES if code != "leverage_min")

    # Re-running the downgrade leaves the operator's generation in place.
    command.downgrade(config, PREVIOUS_REVISION)
    remaining = _rows(migrated_postgres_schema)
    assert [row["proposed_by"] for row in remaining] == ["operator:maker"]

    # Clean up the operator row so the fixture's teardown re-upgrade is exact,
    # then return to head for the fixture's final downgrade.
    with migrated_postgres_schema.app_engine.begin() as connection:
        connection.execute(
            text("DELETE FROM regulatory_parameter WHERE proposed_by = 'operator:maker'")
        )
    with monkeypatch.context() as catalogue:
        catalogue.setattr(regulatory_parameters, "SEED_PARAMETERS", ())
        catalogue.setattr(regulatory_parameters, "CRD_CAPITAL_FLOOR_SEEDS", ())
        catalogue.setattr(regulatory_parameters, "SDI_RECOGNITION_CAP_SEEDS", ())
        command.upgrade(config, REVISION)
    _assert_seeded(_rows(migrated_postgres_schema))
    command.upgrade(config, "head")
    _assert_seeded(_rows(migrated_postgres_schema))
