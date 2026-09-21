"""Postgres proof for the P5 revision (202609190061).

Two halves, both worth a real database rather than the hermetic suite:

* **the seed** — twenty-six governed codes, seventeen of them structural
  tables. A body that was inserted as text, or that lost a nested null on the
  way through a JSON column, looks correct in Python and wrong in Postgres.
  The seed must also leave an operator-approved generation of the same code
  alone: a duplicate does not raise, it takes the day before and quietly
  becomes the ACTIVE generation of a row nobody proposed (D-053).
* **the constraint widenings** — ``irr_sf`` on the run table and the three
  Standardised Framework sections on the line-item table. The hermetic suite
  builds its schema with ``create_all`` and so never executes this revision;
  only a migrated database proves the CHECKs actually admit the new values and
  still reject an unknown one.
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
from sqlalchemy.exc import IntegrityError

from alembic import command
from app.services.regulatory_parameters import P5_SEED_PARAMETERS
from tests.db.test_postgres_migrations import (
    MigratedPostgresSchema,
    alembic_config_for_app,
    migrated_postgres_schema,
)

__all__ = ["migrated_postgres_schema"]

pytestmark = pytest.mark.committing_db

PREVIOUS_REVISION = "202609190060"
REVISION = "202609190061"
CODES = tuple(spec.param_code for spec in P5_SEED_PARAMETERS)
EXPECTED = {
    spec.param_code: (
        None if spec.value is None else Decimal(spec.value),
        None if spec.value_json is None else dict(spec.value_json),
        spec.unit,
        spec.confirmation_status,
        spec.source_citation,
    )
    for spec in P5_SEED_PARAMETERS
}

requires_postgres = pytest.mark.skipif(
    os.getenv("TEST_DATABASE_URL") is None,
    reason="TEST_DATABASE_URL is required for Postgres migration tests.",
)


def _migration() -> ModuleType:
    path = (
        Path(__file__).parents[2]
        / "alembic"
        / "versions"
        / "202609190061_icaap_p5_irrbb_sf_and_granularity.py"
    )
    spec = importlib.util.spec_from_file_location("mig_202609190061", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _rows(schema: MigratedPostgresSchema) -> list[dict[str, Any]]:
    with schema.app_engine.begin() as connection:
        rows = connection.execute(
            text(
                """
                SELECT param_code, scope_type, scope_key, jurisdiction_code, value_numeric,
                       value_json, unit, source_citation, confirmation_status, status,
                       proposed_by, approved_by, effective_from, effective_to
                FROM regulatory_parameter
                WHERE param_code = ANY(:codes)
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
        # The guideline is an exposure draft and the granularity calibration has
        # no published basis: nothing here may present as confirmed (D-039).
        assert row["confirmation_status"] == "pending", code


@requires_postgres
def test_the_seed_is_idempotent_and_leaves_an_operator_generation_alone(
    migrated_postgres_schema: MigratedPostgresSchema,
) -> None:
    config = alembic_config_for_app()

    _assert_seeded(_rows(migrated_postgres_schema))

    command.downgrade(config, PREVIOUS_REVISION)
    assert _rows(migrated_postgres_schema) == []
    # The Pillar 2 threshold the framework REUSES must survive: this revision
    # never seeded it, so its downgrade must not take it.
    with migrated_postgres_schema.app_engine.begin() as connection:
        shared = connection.scalar(
            text(
                "SELECT count(*) FROM regulatory_parameter "
                "WHERE param_code = 'irrbb_outlier_threshold_pct_tier1'"
            )
        )
    assert shared == 1

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
                    (:id, 'institution_class', 'bank', 'irrbb_sf_nii_horizon_months', 'GH',
                     6, NULL, 'months', 'Operator-approved generation (test)',
                     'confirmed', :effective_from, NULL, 'approved',
                     'operator:maker', 'operator:checker', :now, 'test',
                     :now, :now)
                """
            ),
            {"id": uuid4(), "effective_from": date(2024, 1, 1), "now": now},
        )

    command.upgrade(config, REVISION)
    rows = _rows(migrated_postgres_schema)
    horizon = [row for row in rows if row["param_code"] == "irrbb_sf_nii_horizon_months"]
    assert [row["proposed_by"] for row in horizon] == ["operator:maker"]
    assert horizon[0]["value_numeric"] == Decimal(6)
    seeded_codes = sorted(
        row["param_code"] for row in rows if row["proposed_by"] == "platform_seed"
    )
    assert seeded_codes == sorted(
        code for code in CODES if code != "irrbb_sf_nii_horizon_months"
    )

    with migrated_postgres_schema.app_engine.begin() as connection:
        connection.execute(
            text("DELETE FROM regulatory_parameter WHERE proposed_by = 'operator:maker'")
        )
    # Down and up again, not ``upgrade("head")``: this revision IS head, so an
    # upgrade to head here is a no-op and the code the operator row suppressed
    # would never be seeded. Re-running the revision is the thing under test.
    command.downgrade(config, PREVIOUS_REVISION)
    command.upgrade(config, REVISION)
    _assert_seeded(_rows(migrated_postgres_schema))

    with migrated_postgres_schema.app_engine.begin() as connection:
        generations = connection.execute(
            text(
                "SELECT param_code, count(*) FROM regulatory_parameter "
                "WHERE param_code = ANY(:codes) GROUP BY param_code"
            ),
            {"codes": list(CODES)},
        ).all()
    assert {code: count for code, count in generations} == dict.fromkeys(CODES, 1)


@requires_postgres
def test_the_widened_checks_admit_the_framework_and_still_reject_an_unknown_value(
    migrated_postgres_schema: MigratedPostgresSchema,
) -> None:
    module = _migration()
    assert "'irr_sf'" in module._RUNS_NEW  # noqa: SLF001 - the pinned expression
    assert "'irr_sf'" not in module._RUNS_OLD  # noqa: SLF001

    # Qualified by the TABLE, not by the constraint name: every disposable test
    # schema on this server carries a constraint of the same name, and an
    # unqualified lookup returns whichever one Postgres reaches first — which is
    # how this test first "proved" a constraint from somebody else's schema.
    with migrated_postgres_schema.app_engine.begin() as connection:
        constraint = connection.scalar(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = 'ck_regulatory_runs_module' "
                "AND conrelid = 'regulatory_runs'::regclass"
            )
        )
        sections = connection.scalar(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = 'ck_regulatory_line_items_section' "
                "AND conrelid = 'regulatory_line_items'::regclass"
            )
        )
    assert constraint is not None
    assert "irr_sf" in constraint
    # The credit module's value proves the lookup found the CURRENT definition
    # rather than a stale one from another schema.
    assert "credit" in constraint

    assert sections is not None
    for section in ("irr_sf_ladder", "irr_sf_eve", "irr_sf_nii"):
        assert section in sections
    # Still a real gate, not a rubber stamp.
    assert "irr_sf_options" not in sections


_RUN_INSERT = (
    "INSERT INTO regulatory_runs "
    "(id, organization_id, bank_id, reporting_period_id, module, "
    "scenario_code, status, engine_version, input_schema_version, "
    "output_schema_version, input_hash, inputs, metrics, created_by, "
    "created_at, updated_at) VALUES "
    "(:id, 'OR-NONE0001', 'BK-NONE0001', :period, :module, "
    "'standardised_framework', 'queued', 'v', 'v', 'v', 'h', '{}', '{}', "
    ":user, now(), now())"
)


@requires_postgres
def test_a_run_row_carrying_the_new_module_value_is_accepted(
    migrated_postgres_schema: MigratedPostgresSchema,
) -> None:
    """The CHECK is proven by a row, not only by its printed definition.

    Both halves are discriminated BY THE CONSTRAINT THAT FIRES, because the
    fabricated organization/bank/period/user ids violate foreign keys as well.
    Until 2026-09-20 this test inserted only the INVALID value and asserted a
    bare ``IntegrityError``: dropping ``ck_regulatory_runs_module`` entirely
    left it green on the foreign keys, and the "accepted" half its NAME
    promises was never written at all.

    Postgres evaluates CHECK constraints while forming the tuple and fires
    referential-integrity triggers afterwards, so the constraint that names
    itself in the error is the one that decided.
    """
    engine = migrated_postgres_schema.app_engine

    # Accepted: ``irr_sf`` passes the CHECK, so the row gets as far as the
    # foreign keys. If the widening had not landed, the CHECK would fire first
    # and name itself.
    with engine.begin() as connection, pytest.raises(IntegrityError) as accepted:
        connection.execute(
            text(_RUN_INSERT),
            {"id": uuid4(), "period": uuid4(), "user": uuid4(), "module": "irr_sf"},
        )
    message = str(accepted.value.orig)
    assert "ck_regulatory_runs_module" not in message, (
        "the module CHECK refused 'irr_sf' — the P5 widening did not land"
    )
    assert "foreign key constraint" in message, message

    # Rejected: an unknown value is refused by the module CHECK, named.
    with engine.begin() as connection, pytest.raises(IntegrityError) as rejected:
        connection.execute(
            text(_RUN_INSERT),
            {"id": uuid4(), "period": uuid4(), "user": uuid4(), "module": "irr_sf_typo"},
        )
    assert "ck_regulatory_runs_module" in str(rejected.value.orig)
