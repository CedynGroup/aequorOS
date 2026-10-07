"""The governed register starts empty without changing legacy parameters or saved runs."""

from __future__ import annotations

import importlib.util
import os
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import MetaData, Table, create_engine, text
from sqlalchemy.exc import IntegrityError

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
def test_upgrade_leaves_legacy_presets_without_approved_versions(
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
        assert connection.scalar(text("SELECT count(*) FROM forecast_assumption_versions")) == 0
        assert (
            connection.scalar(
                text("SELECT count(*) FROM param_stress_shock WHERE module = 'forecast'")
            )
            == len(SCENARIOS) * len(KEYS) + 1
        )

    with migrated_postgres_schema.app_engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.organization_id', :org, true)"), {"org": INCOMPLETE_ORG}
        )
        assert connection.scalar(text("SELECT count(*) FROM forecast_assumption_versions")) == 0

    assert migrated_postgres_schema.policies({"forecast_assumption_versions"}) == {
        "forecast_assumption_versions_tenant_isolation"
    }


def test_sqlite_upgrade_preserves_legacy_rows_and_requires_a_checker() -> None:
    path = (
        Path(__file__).parents[2] / "alembic/versions/202610070084_forecast_assumption_versions.py"
    )
    spec = importlib.util.spec_from_file_location("forecast_assumption_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        for statement in (
            "CREATE TABLE organizations (id TEXT PRIMARY KEY)",
            "CREATE TABLE banks (id TEXT PRIMARY KEY, organization_id TEXT, "
            "jurisdiction_code TEXT)",
            "CREATE TABLE regulatory_runs (id TEXT PRIMARY KEY, input_hash TEXT, "
            "input_snapshot TEXT, metrics TEXT)",
            "CREATE TABLE param_stress_shock (id TEXT PRIMARY KEY, organization_id TEXT, "
            "jurisdiction_code TEXT, module TEXT, scenario_code TEXT, shock_key TEXT, "
            "shock_value TEXT, effective_from TEXT, effective_to TEXT, approved_by TEXT, "
            "approval_timestamp TEXT)",
        ):
            connection.exec_driver_sql(statement)
        connection.execute(text("INSERT INTO organizations VALUES (:org)"), {"org": ORG})
        connection.execute(
            text("INSERT INTO banks VALUES (:bank, :org, 'GH')"),
            {"bank": BANKS[0], "org": ORG},
        )
        for scenario in SCENARIOS:
            for key in KEYS:
                connection.execute(
                    text(
                        "INSERT INTO param_stress_shock VALUES "
                        "(:id, :org, 'GH', 'forecast', :scenario, :key, '4.8', "
                        "'2000-01-01', NULL, 'Legacy board label', '2025-01-01 00:00:00')"
                    ),
                    {"id": str(uuid4()), "org": ORG, "scenario": scenario, "key": key},
                )
        connection.exec_driver_sql(
            "INSERT INTO regulatory_runs VALUES "
            "('saved', 'value-hash', '{\"nim_pct\":\"4.8\"}', '{\"nii\":123}')"
        )
        legacy = connection.execute(text("SELECT * FROM param_stress_shock ORDER BY id")).all()
        saved = connection.execute(text("SELECT * FROM regulatory_runs")).one()
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        assert connection.scalar(text("SELECT count(*) FROM forecast_assumption_versions")) == 0
        assert (
            connection.execute(text("SELECT * FROM param_stress_shock ORDER BY id")).all() == legacy
        )
        assert (
            connection.execute(
                text("SELECT id, input_hash, input_snapshot, metrics FROM regulatory_runs")
            ).one()
            == saved
        )
        versions = Table("forecast_assumption_versions", MetaData(), autoload_with=connection)
        proposed = {
            "id": uuid4().hex,
            "organization_id": ORG,
            "bank_id": BANKS[0],
            "version_number": 1,
            "status": "approved",
            "effective_from": date(2000, 1, 1),
            "presets": {},
            "change_note": "Governed proposal",
            "created_by": uuid4().hex,
            "reviewed_at": APPROVED_AT,
            "created_at": APPROVED_AT,
            "updated_at": APPROVED_AT,
        }
        with pytest.raises(IntegrityError):
            connection.execute(versions.insert(), proposed)
        connection.execute(versions.insert(), {**proposed, "reviewed_by": uuid4().hex})
        assert connection.scalar(text("SELECT count(*) FROM forecast_assumption_versions")) == 1
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
        assert connection.execute(text("SELECT * FROM regulatory_runs")).one() == saved
    engine.dispose()
