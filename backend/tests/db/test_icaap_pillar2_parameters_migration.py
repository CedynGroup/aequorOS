"""Postgres proof for the ICAAP Pillar 2 parameter seed (202609190056).

Twenty governed codes reach the Pillar 2 engine from the control plane (D-024),
eleven of them as structural tables. The migration must seed exactly the
catalogue's rows and bodies, must not touch an operator-approved generation of
the same code, must be idempotent, and its downgrade must remove only the rows
the seed itself wrote.

The structural half is the part worth proving against a real database rather
than in the hermetic suite: ``value_json`` bodies round-trip through a JSON
column, and a seed that inserted them as text, or dropped a nested null, would
look fine in Python and wrong in Postgres.
"""

from __future__ import annotations

import importlib.util
import os
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text

from alembic import command
from app.services.icaap.parameters import ICAAP_PARAM_CODES
from app.services.regulatory_parameters import (
    ICAAP_P2_SEED_PARAMETERS,
    P2_PARAMETER_CODES,
    SEED_EFFECTIVE_FROM,
)
from tests.db.test_postgres_migrations import (
    MigratedPostgresSchema,
    alembic_config_for_app,
    migrated_postgres_schema,
)

__all__ = ["migrated_postgres_schema"]

pytestmark = pytest.mark.committing_db

PREVIOUS_REVISION = "202609190055"
REVISION = "202609190056"
CODES = tuple(spec.param_code for spec in ICAAP_P2_SEED_PARAMETERS)
EXPECTED = {
    spec.param_code: (
        None if spec.value is None else Decimal(spec.value),
        None if spec.value_json is None else dict(spec.value_json),
        spec.unit,
        spec.confirmation_status,
        spec.source_citation,
    )
    for spec in ICAAP_P2_SEED_PARAMETERS
}


def _migration() -> ModuleType:
    path = (
        Path(__file__).parents[2]
        / "alembic"
        / "versions"
        / "202609190056_icaap_pillar2_parameters.py"
    )
    spec = importlib.util.spec_from_file_location("mig_202609190056", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _rows(schema: MigratedPostgresSchema) -> list[dict[str, Any]]:
    """The Ghanaian generations only: ``202609200063`` seeds the same codes for NG/KE."""
    with schema.app_engine.begin() as connection:
        rows = connection.execute(
            text(
                """
                SELECT param_code, scope_type, scope_key, jurisdiction_code, value_numeric,
                       value_json, unit, source_citation, confirmation_status, status,
                       proposed_by, approved_by, effective_from, effective_to
                FROM regulatory_parameter
                WHERE param_code = ANY(:codes) AND jurisdiction_code = 'GH'
                ORDER BY param_code, effective_from
                """
            ),
            {"codes": list(CODES)},
        ).mappings()
        return [dict(row) for row in rows]


def _assert_seeded(rows: list[dict[str, Any]]) -> None:
    seeded = [row for row in rows if row["proposed_by"] == "platform_seed"]
    assert sorted(row["param_code"] for row in seeded) == sorted(CODES)
    for row in seeded:
        code = row["param_code"]
        value, body, unit, confirmation, citation = EXPECTED[code]
        assert (row["scope_type"], row["scope_key"]) == ("institution_class", "bank")
        assert row["jurisdiction_code"] == "GH"
        assert row["value_numeric"] == value, code
        assert row["value_json"] == body, code
        assert (row["unit"], row["confirmation_status"]) == (unit, confirmation), code
        assert row["source_citation"] == citation, code
        assert (row["status"], row["effective_to"]) == ("approved", None)
        assert row["approved_by"] == "platform_seed"
        # D-039: an unsourced calibration says so and is never 'confirmed'.
        if citation.startswith("REPRESENTATIVE:"):
            assert row["confirmation_status"] == "pending", code


@pytest.mark.skipif(
    os.getenv("TEST_DATABASE_URL") is None,
    reason="TEST_DATABASE_URL is required for Postgres migration tests.",
)
def test_seed_is_idempotent_and_its_downgrade_removes_only_its_own_rows(
    migrated_postgres_schema: MigratedPostgresSchema,
) -> None:
    config = alembic_config_for_app()

    _assert_seeded(_rows(migrated_postgres_schema))

    # Down to the workspace revision: the Pillar 2 rows go, the workspace's stay.
    command.downgrade(config, PREVIOUS_REVISION)
    assert _rows(migrated_postgres_schema) == []
    with migrated_postgres_schema.app_engine.begin() as connection:
        surviving = set(
            connection.scalars(
                text(
                    "SELECT param_code FROM regulatory_parameter "
                    "WHERE param_code = ANY(:codes) AND jurisdiction_code = 'GH'"
                ),
                {"codes": sorted(ICAAP_PARAM_CODES)},
            )
        )
    assert surviving == set(ICAAP_PARAM_CODES)

    # An operator-approved generation of one structural code exists before the
    # upgrade runs: it must survive untouched and must not gain a seeded twin.
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
                    (:id, 'institution_class', 'bank', 'ccr_name_cr_n', 'GH',
                     25, NULL, 'count', 'Operator-approved generation (test)',
                     'confirmed', :effective_from, NULL, 'approved',
                     'operator:maker', 'operator:checker', :now, 'test',
                     :now, :now)
                """
            ),
            {"id": uuid4(), "effective_from": date(2024, 1, 1), "now": now},
        )

    command.upgrade(config, REVISION)
    rows = _rows(migrated_postgres_schema)
    cr_n = [row for row in rows if row["param_code"] == "ccr_name_cr_n"]
    assert [row["proposed_by"] for row in cr_n] == ["operator:maker"]
    assert cr_n[0]["value_numeric"] == Decimal(25)
    seeded_codes = sorted(
        row["param_code"] for row in rows if row["proposed_by"] == "platform_seed"
    )
    assert seeded_codes == sorted(code for code in CODES if code != "ccr_name_cr_n")

    # Re-running the downgrade leaves the operator's generation in place.
    command.downgrade(config, PREVIOUS_REVISION)
    remaining = _rows(migrated_postgres_schema)
    assert [row["proposed_by"] for row in remaining] == ["operator:maker"]

    with migrated_postgres_schema.app_engine.begin() as connection:
        connection.execute(
            text("DELETE FROM regulatory_parameter WHERE proposed_by = 'operator:maker'")
        )
    command.upgrade(config, "head")
    _assert_seeded(_rows(migrated_postgres_schema))
    _assert_one_generation_per_icaap_code(migrated_postgres_schema)


def _assert_one_generation_per_icaap_code(schema: MigratedPostgresSchema) -> None:
    """The whole point of the P1/P2 split: one generation per ICAAP code.

    A code seeded by both migrations would not raise — the second insert takes
    the day before — it would quietly become the ACTIVE generation and supersede
    a row nobody proposed. So the count is asserted against the database, not
    only against the catalogues. Folded into the test above rather than given a
    fixture of its own: each one costs a full migration chain.
    """
    codes = sorted(P2_PARAMETER_CODES | ICAAP_PARAM_CODES)
    with schema.app_engine.begin() as connection:
        rows = connection.execute(
            text(
                """
                SELECT param_code, count(*) AS generations
                FROM regulatory_parameter
                WHERE param_code = ANY(:codes) AND jurisdiction_code = 'GH'
                GROUP BY param_code
                ORDER BY param_code
                """
            ),
            {"codes": codes},
        ).mappings()
        counts = {row["param_code"]: row["generations"] for row in rows}
    assert sorted(counts) == codes
    assert {code: count for code, count in counts.items() if count != 1} == {}


def test_the_migration_chains_after_the_workspace_and_pins_its_own_rows() -> None:
    module = _migration()
    assert (module.revision, module.down_revision) == (REVISION, PREVIOUS_REVISION)
    assert module.EFFECTIVE_FROM == SEED_EFFECTIVE_FROM
    assert set(module.PARAM_CODES) == set(P2_PARAMETER_CODES)
    assert set(module.PARAM_CODES) & set(ICAAP_PARAM_CODES) == set()
    source = Path(module.__file__ or "").read_text(encoding="utf-8")
    body = source.split('"""', 2)[2]
    assert "seed_rows" not in body
    assert "SEED_PARAMETERS" not in body
