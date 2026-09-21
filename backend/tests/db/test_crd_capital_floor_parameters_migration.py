"""Postgres proof for the CRD capital-floor control-plane seed (202609190054).

Regulatory audit B2 / M21 (ICAAP P0): ``cet1_min``, ``tier1_min``,
``leverage_min`` (6, fixing the 3% register default), the CCB1 / CCyB / D-SIB
buffers and the AT1 / Tier 2 recognition caps become governed bank-class rows.
The migration must seed exactly the catalogue values, be idempotent against a
row already present for the same scope (an operator-approved generation is
never duplicated or overwritten), and its downgrade must remove only the rows
the seed itself wrote.
"""

from __future__ import annotations

import importlib.util
import os
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from uuid import uuid4

import pytest
from sqlalchemy import text

from alembic import command
from app.services.regulatory_parameters import (
    CRD_CAPITAL_FLOOR_SEEDS,
    SDI_RECOGNITION_CAP_SEEDS,
    SEED_EFFECTIVE_FROM,
    SEED_PARAMETERS,
)
from tests.db.test_postgres_migrations import (
    MigratedPostgresSchema,
    alembic_config_for_app,
    migrated_postgres_schema,
)

__all__ = ["migrated_postgres_schema"]

pytestmark = pytest.mark.committing_db

PREVIOUS_REVISION = "202609160053"
REVISION = "202609190054"
CODES = (
    "cet1_min",
    "tier1_min",
    "leverage_min",
    "ccb1_pct",
    "ccyb_pct",
    "dsib_buffer_pct",
    "at1_cap_pct_rwa",
    "tier2_cap_pct_rwa",
)
#: D-024: CET1 / Tier 1 await stakeholder confirmation of the conservation-buffer
#: treatment (audit item M20); every other bank seed is confirmed.
PENDING = frozenset({"cet1_min", "tier1_min"})
#: D-042: the SDI class carries the two recognition caps at the pre-2026-09-19
#: platform values, pending.
SDI_EXPECTED = {"at1_cap_pct_rwa": Decimal("1.5"), "tier2_cap_pct_rwa": Decimal("2")}
EXPECTED = {
    "cet1_min": Decimal("6.5"),
    "tier1_min": Decimal("8"),
    "leverage_min": Decimal("6"),
    "ccb1_pct": Decimal("3"),
    "ccyb_pct": Decimal("0"),
    "dsib_buffer_pct": Decimal("0"),
    "at1_cap_pct_rwa": Decimal("1.5"),
    "tier2_cap_pct_rwa": Decimal("2"),
}


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


def _migration() -> ModuleType:
    path = (
        Path(__file__).parents[2]
        / "alembic"
        / "versions"
        / "202609190054_crd_capital_floor_parameters.py"
    )
    spec = importlib.util.spec_from_file_location("mig_202609190054", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_catalogue_and_the_migration_agree() -> None:
    """The migration pins its values (it never imports the live catalogue), and
    today the pinned rows equal the catalogue's exactly — value, citation, unit,
    scope and generation date — so a fresh database seeded by the initial
    control-plane migration and an existing one seeded by this revision agree."""
    specs = {
        spec.param_code: spec
        for spec in SEED_PARAMETERS
        if spec.param_code in CODES and spec.scope_key == "bank"
    }
    assert set(specs) == set(CODES)
    sdi_specs = {
        spec.param_code: spec
        for spec in SEED_PARAMETERS
        if spec.param_code in CODES and spec.scope_key == "sdi"
    }
    # Every capital floor is a scalar percent, so a missing ``value`` (the
    # field is optional since ``value_json`` landed) is itself the defect.
    assert all(spec.value is not None for spec in sdi_specs.values())
    assert {
        code: Decimal(spec.value) for code, spec in sdi_specs.items() if spec.value is not None
    } == SDI_EXPECTED
    assert {spec.confirmation_status for spec in sdi_specs.values()} == {"pending"}
    for code, spec in specs.items():
        assert spec.value is not None, code
        assert Decimal(spec.value) == EXPECTED[code]
        assert (spec.scope_type, spec.scope_key, spec.unit) == (
            "institution_class",
            "bank",
            "percent",
        )
        assert spec.confirmation_status == ("pending" if code in PENDING else "confirmed")
    migration = _migration()
    source = Path(migration.__file__ or "").read_text(encoding="utf-8")
    assert "seed_rows" not in source and "SEED_PARAMETERS" not in source.split('"""', 2)[2]
    assert tuple(migration.SEEDS) == CRD_CAPITAL_FLOOR_SEEDS
    assert tuple(migration.SDI_SEEDS) == SDI_RECOGNITION_CAP_SEEDS
    assert migration.EFFECTIVE_FROM == SEED_EFFECTIVE_FROM
    assert tuple(migration.PARAM_CODES) == CODES


@pytest.mark.skipif(
    os.getenv("TEST_DATABASE_URL") is None,
    reason="TEST_DATABASE_URL is required for Postgres migration tests.",
)
def test_seed_is_idempotent_and_its_downgrade_removes_only_its_own_rows(
    migrated_postgres_schema: MigratedPostgresSchema,
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
    command.upgrade(config, "head")
    _assert_seeded(_rows(migrated_postgres_schema))
