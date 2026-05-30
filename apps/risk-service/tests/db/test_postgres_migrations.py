from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url

from alembic import command
from app.core.config import get_settings
from app.db.session import get_engine


@dataclass(frozen=True)
class MigratedPostgresSchema:
    app_engine: Engine
    schema_name: str

    def constraints(self, names: set[str]) -> set[str]:
        placeholders = ", ".join(f"'{name}'" for name in sorted(names))
        with self.app_engine.connect() as connection:
            return set(
                connection.execute(
                    text(
                        f"""
                        SELECT conname
                        FROM pg_constraint
                        WHERE conname IN ({placeholders})
                        """
                    )
                ).scalars()
            )

    def policies(self, table_names: set[str]) -> set[str]:
        with self.app_engine.connect() as connection:
            return set(
                connection.execute(
                    text(
                        """
                        SELECT policyname
                        FROM pg_policies
                        WHERE schemaname = :schema_name
                          AND tablename = ANY(:table_names)
                        """
                    ),
                    {
                        "schema_name": self.schema_name,
                        "table_names": sorted(table_names),
                    },
                ).scalars()
            )


def postgres_schema_url(database_url: str, schema_name: str) -> str:
    url = make_url(database_url)
    return url.update_query_dict({"options": f"-csearch_path={schema_name}"}).render_as_string(
        hide_password=False
    )


@pytest.fixture
def migrated_postgres_schema(monkeypatch: pytest.MonkeyPatch) -> Iterator[MigratedPostgresSchema]:
    test_database_url = os.environ["TEST_DATABASE_URL"]
    if not make_url(test_database_url).drivername.startswith("postgresql"):
        pytest.skip("TEST_DATABASE_URL must point to Postgres.")

    schema_name = f"risk_service_migration_{uuid4().hex}"
    database_url = postgres_schema_url(test_database_url, schema_name)
    alembic_config = alembic_config_for_app()
    admin_engine = create_engine(test_database_url, isolation_level="AUTOCOMMIT")
    app_engine = create_engine(database_url)
    monkeypatch.setenv("DATABASE_URL", database_url)
    clear_database_caches()

    with admin_engine.connect() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))

    try:
        command.upgrade(alembic_config, "head")
        yield MigratedPostgresSchema(app_engine=app_engine, schema_name=schema_name)
        command.downgrade(alembic_config, "base")
    finally:
        clear_database_caches()
        app_engine.dispose()
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        admin_engine.dispose()


def alembic_config_for_app() -> Config:
    app_root = Path(__file__).parents[2]
    alembic_config = Config(str(app_root / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(app_root / "alembic"))
    return alembic_config


def clear_database_caches() -> None:
    get_settings.cache_clear()
    get_engine.cache_clear()


@pytest.mark.skipif(
    os.getenv("TEST_DATABASE_URL") is None,
    reason="TEST_DATABASE_URL is required for Postgres migration smoke tests.",
)
def test_postgres_migrations_create_workflow_constraints_and_rls(
    migrated_postgres_schema: MigratedPostgresSchema,
) -> None:
    assert migrated_postgres_schema.constraints(
        {
            "ck_risk_cases_status",
            "ck_risk_cases_decision",
            "ck_risk_scores_score",
            "ck_risk_findings_source",
        }
    ) == {
        "ck_risk_cases_status",
        "ck_risk_cases_decision",
        "ck_risk_scores_score",
        "ck_risk_findings_source",
    }
    assert migrated_postgres_schema.policies({"risk_scores", "risk_case_decisions"}) == {
        "risk_scores_tenant_isolation",
        "risk_case_decisions_tenant_isolation",
    }
