"""Postgres proof for the governed forecast assumption register (202610070084).

A bank that could forecast before the revision keeps forecasting on the same
values: every effective date at which its organization's register resolved a
COMPLETE preset set becomes an approved version for each of its banks in that
jurisdiction. An incomplete set is not carried over, and the table is FORCE-RLS.
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import text

from alembic import command
from tests.db.test_initial_owner_migration import _insert_organization
from tests.db.test_postgres_migrations import (
    MigratedPostgresSchema,
    alembic_config_for_app,
    clear_database_caches,
    migrated_postgres_schema,
)

__all__ = ["migrated_postgres_schema"]

pytestmark = pytest.mark.committing_db

PREVIOUS_REVISION = "202610040083"
REVISION = "202610070084"
ORG = "OR-FCAS0001"
INCOMPLETE_ORG = "OR-FCAS0002"
BANKS = ("BK-FCAS0001", "BK-FCAS0002")
KEYS = (
    "loan_growth_pct",
    "deposit_growth_pct",
    "nim_pct",
    "cost_to_income_pct",
    "credit_loss_rate_pct",
    "fx_depreciation_pct",
    "dividend_payout_pct",
)
SCENARIOS = ("base", "adverse", "severely_adverse")
APPROVED_AT = datetime(2025, 1, 1, tzinfo=UTC)
REVISED_AT = datetime(2026, 3, 1, tzinfo=UTC)


def _insert_bank(connection, organization_id: str, bank_id: str) -> None:
    connection.execute(
        text(
            """
            INSERT INTO banks
              (id, organization_id, name, short_name, currency, jurisdiction_code,
               license_type, institution_type, created_at, updated_at)
            VALUES
              (:bank_id, :organization_id, :bank_id, :bank_id, 'GHS', 'GH',
               'universal', 'universal_bank', now(), now())
            """
        ),
        {"bank_id": bank_id, "organization_id": organization_id},
    )


def _insert_preset(  # noqa: PLR0913 - one register row, every identity column explicit
    connection,
    organization_id: str,
    scenario: str,
    key: str,
    value: str,
    effective_from: date,
    approved_at: datetime,
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO param_stress_shock
              (id, organization_id, jurisdiction_code, module, scenario_code, shock_key,
               shock_value, effective_from, approved_by, approval_timestamp,
               created_at, updated_at)
            VALUES
              (gen_random_uuid(), :org, 'GH', 'forecast', :scenario, :key, :value,
               :effective_from, :approved_by, :approved_at, now(), now())
            """
        ),
        {
            "org": organization_id,
            "scenario": scenario,
            "key": key,
            "value": value,
            "effective_from": effective_from,
            "approved_by": f"Board {approved_at.year}",
            "approved_at": approved_at,
        },
    )


@pytest.mark.skipif(
    os.getenv("TEST_DATABASE_URL") is None,
    reason="TEST_DATABASE_URL is required for Postgres migration tests.",
)
def test_complete_register_sets_become_approved_versions_per_bank(
    migrated_postgres_schema: MigratedPostgresSchema,
) -> None:
    config = alembic_config_for_app()
    command.downgrade(config, PREVIOUS_REVISION)
    clear_database_caches()
    now = datetime.now(UTC)

    with migrated_postgres_schema.app_engine.begin() as connection:
        _insert_organization(connection, ORG, now)
        for bank_id in BANKS:
            _insert_bank(connection, ORG, bank_id)
        for scenario in SCENARIOS:
            for key in KEYS:
                _insert_preset(
                    connection, ORG, scenario, key, "4.80", date(2000, 1, 1), APPROVED_AT
                )
        # A later generation revises one value; the rest carry forward.
        _insert_preset(connection, ORG, "base", "nim_pct", "5.5", date(2026, 3, 1), REVISED_AT)
    with migrated_postgres_schema.app_engine.begin() as connection:
        _insert_organization(connection, INCOMPLETE_ORG, now)
        _insert_bank(connection, INCOMPLETE_ORG, "BK-FCAS0003")
        _insert_preset(
            connection, INCOMPLETE_ORG, "base", "nim_pct", "4", date(2000, 1, 1), APPROVED_AT
        )

    command.upgrade(config, REVISION)
    clear_database_caches()

    with migrated_postgres_schema.app_engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.organization_id', :org, true)"), {"org": ORG}
        )
        versions = (
            connection.execute(
                text(
                    """
                SELECT bank_id, version_number, status, origin, effective_from,
                       presets, approver_label, reviewed_at, reviewed_by
                FROM forecast_assumption_versions
                ORDER BY bank_id, version_number
                """
                )
            )
            .mappings()
            .all()
        )
    assert [(v["bank_id"], v["version_number"]) for v in versions] == [
        (BANKS[0], 1),
        (BANKS[0], 2),
        (BANKS[1], 1),
        (BANKS[1], 2),
    ]
    first, second = versions[0], versions[1]
    assert (first["status"], first["origin"], first["reviewed_by"]) == (
        "approved",
        "register",
        None,
    )
    assert (first["effective_from"], first["approver_label"]) == (date(2000, 1, 1), "Board 2025")
    assert first["reviewed_at"] == APPROVED_AT
    assert first["presets"]["base"]["nim_pct"] == "4.8"
    assert (second["effective_from"], second["approver_label"]) == (date(2026, 3, 1), "Board 2026")
    assert second["presets"]["base"]["nim_pct"] == "5.5"
    assert second["presets"]["adverse"]["nim_pct"] == "4.8"

    with migrated_postgres_schema.app_engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.organization_id', :org, true)"), {"org": INCOMPLETE_ORG}
        )
        assert connection.scalar(text("SELECT count(*) FROM forecast_assumption_versions")) == 0

    assert migrated_postgres_schema.policies({"forecast_assumption_versions"}) == {
        "forecast_assumption_versions_tenant_isolation"
    }
