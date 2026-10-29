"""Execute the stress backfill against old, partial and revised registers."""

from __future__ import annotations

import importlib.util
import os
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Protocol, cast

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.orm import Session

from alembic import command
from app.db.session import force_rls_suspended
from app.models import Bank, Organization, ParamStressShock
from tests.db.test_postgres_migrations import (
    MigratedPostgresSchema,
    alembic_config_for_app,
    clear_database_caches,
    forward_migrated_postgres_schema,
)
from tests.support.database import managed_engine

__all__ = ["forward_migrated_postgres_schema"]


class _Migration(Protocol):
    def upgrade(self) -> None: ...
    def downgrade(self) -> None: ...


def test_backfill_is_scoped_additive_and_idempotent(request: pytest.FixtureRequest) -> None:
    spec = importlib.util.spec_from_file_location(
        "bank_stress_defaults",
        Path(__file__).parents[2] / "alembic/versions/202610240001_bank_stress_defaults.py",
    )
    assert spec is not None and spec.loader is not None
    migration = cast(_Migration, importlib.util.module_from_spec(spec))
    spec.loader.exec_module(cast(ModuleType, migration))
    with managed_engine(request, "sqlite://").begin() as connection:
        connection.execute(
            text("CREATE TABLE institution_types (type_code TEXT, institution_class TEXT)")
        )
        connection.execute(
            text(
                "CREATE TABLE banks (organization_id TEXT, jurisdiction_code TEXT, "
                "institution_type TEXT)"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE param_stress_shock (id TEXT PRIMARY KEY, organization_id TEXT, "
                "jurisdiction_code TEXT, module TEXT, scenario_code TEXT, shock_key TEXT, "
                "shock_value NUMERIC, effective_from DATE, approved_by TEXT, "
                "approval_timestamp TIMESTAMP, created_at TIMESTAMP, updated_at TIMESTAMP)"
            )
        )
        connection.execute(
            text("INSERT INTO institution_types VALUES ('universal', 'bank'), ('savings', 'sdi')")
        )
        connection.execute(
            text(
                "INSERT INTO banks VALUES ('OR-ONE', 'GH', 'universal'), "
                "('OR-ONE', 'GH', 'universal'), ('OR-ONE', 'NG', 'universal'), "
                "('OR-TWO', 'GH', 'savings'), ('OR-THREE', 'GH', 'universal')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO param_stress_shock (id, organization_id, jurisdiction_code, "
                "module, scenario_code, shock_key, shock_value, effective_from, approved_by) "
                "VALUES ('custom', 'OR-ONE', 'GH', 'fx', 'severe_depreciation', "
                "'ghs_usd_shock_pct', 27, '2025-01-01', 'board')"
            )
        )
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            before = connection.execute(text("SELECT * FROM param_stress_shock ORDER BY id")).all()
            assert len(before) == 24
            assert (
                connection.scalar(
                    text("SELECT COUNT(*) FROM param_stress_shock WHERE organization_id='OR-TWO'")
                )
                == 0
            )
            custom = connection.execute(
                text("SELECT shock_value, approved_by FROM param_stress_shock WHERE id='custom'")
            ).one()
            assert tuple(custom) == (27, "board")
            scopes = connection.execute(
                text(
                    "SELECT organization_id, jurisdiction_code, COUNT(*) FROM param_stress_shock "
                    "GROUP BY organization_id, jurisdiction_code "
                    "ORDER BY organization_id, jurisdiction_code"
                )
            ).all()
            assert [tuple(row) for row in scopes] == [
                ("OR-ONE", "GH", 8),
                ("OR-ONE", "NG", 8),
                ("OR-THREE", "GH", 8),
            ]
            migration.upgrade()
            migration.downgrade()
            assert (
                connection.execute(text("SELECT * FROM param_stress_shock ORDER BY id")).all()
                == before
            )


@pytest.mark.committing_db
@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="Postgres migration proof")
def test_backfill_sees_all_tenants_and_restores_forced_rls(
    forward_migrated_postgres_schema: MigratedPostgresSchema,
) -> None:
    schema = forward_migrated_postgres_schema
    spec = importlib.util.spec_from_file_location(
        "bank_stress_defaults_pg",
        Path(__file__).parents[2] / "alembic/versions/202610240001_bank_stress_defaults.py",
    )
    assert spec is not None and spec.loader is not None
    migration = cast(_Migration, importlib.util.module_from_spec(spec))
    spec.loader.exec_module(cast(ModuleType, migration))
    with schema.app_engine.begin() as connection, Session(bind=connection) as session:
        for org in ("OR-SHK00001", "OR-SHK00002"):
            session.info["organization_id"] = org
            connection.execute(
                text("SELECT set_config('app.organization_id', :org, true)"), {"org": org}
            )
            session.add(Organization(id=org, name="Stress default test"))
            session.flush()
            session.add(
                Bank(
                    organization_id=org,
                    name="Stress bank",
                    short_name="Stress bank",
                    currency="GHS",
                    jurisdiction_code="GH",
                    license_type="universal",
                    institution_type="universal_bank",
                )
            )
            session.flush()
            session.add_all(
                ParamStressShock(
                    organization_id=org,
                    jurisdiction_code="GH",
                    module=module,
                    scenario_code="existing",
                    shock_key="existing",
                    shock_value=Decimal("1"),
                    effective_from=date(2000, 1, 1),
                    approved_by="board",
                    approval_timestamp=datetime.now(UTC),
                )
                for module in ("irr", "liquidity")
            )
            session.flush()
        connection.execute(text("SELECT set_config('app.organization_id', '', true)"))
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            migration.upgrade()
            migration.downgrade()
        for org in ("OR-SHK00001", "OR-SHK00002"):
            connection.execute(
                text("SELECT set_config('app.organization_id', :org, true)"), {"org": org}
            )
            assert (
                connection.scalar(
                    text("SELECT COUNT(*) FROM param_stress_shock WHERE organization_id=:org"),
                    {"org": org},
                )
                == 10
            )
        forced = connection.execute(
            text(
                "SELECT relname, relforcerowsecurity FROM pg_class "
                "WHERE relnamespace = current_schema()::regnamespace "
                "AND relname IN ('banks', 'param_stress_shock')"
            )
        ).all()
        assert {tuple(row) for row in forced} == {("banks", True), ("param_stress_shock", True)}

    # The data-only revision retains evidence, but crossing the historical
    # IRR/FX/FTP foundation must remove unsupported modules before narrowing.
    command.downgrade(alembic_config_for_app(), "202607160003")
    clear_database_caches()
    with schema.app_engine.begin() as connection:
        with force_rls_suspended(connection, "param_stress_shock"):
            assert connection.execute(
                text("SELECT module, COUNT(*) FROM param_stress_shock GROUP BY module")
            ).all() == [("liquidity", 2)]
        forced = connection.execute(
            text(
                "SELECT relname, relforcerowsecurity FROM pg_class "
                "WHERE relnamespace = current_schema()::regnamespace "
                "AND relname IN ('regulatory_runs', 'bank_financial_facts', "
                "'regulatory_line_items', 'param_stress_shock', 'regulatory_metric_results')"
            )
        ).all()
        assert {tuple(row) for row in forced} == {
            (table, True)
            for table in (
                "regulatory_runs",
                "bank_financial_facts",
                "regulatory_line_items",
                "param_stress_shock",
                "regulatory_metric_results",
            )
        }
