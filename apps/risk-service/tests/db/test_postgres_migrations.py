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
from tests.api.helpers import ORG_1, ORG_2


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

    def tables(self, table_names: set[str]) -> set[str]:
        with self.app_engine.connect() as connection:
            return set(
                connection.execute(
                    text(
                        """
                        SELECT tablename
                        FROM pg_tables
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

    def indexes(self, index_names: set[str]) -> set[str]:
        with self.app_engine.connect() as connection:
            return set(
                connection.execute(
                    text(
                        """
                        SELECT indexname
                        FROM pg_indexes
                        WHERE schemaname = :schema_name
                          AND indexname = ANY(:index_names)
                        """
                    ),
                    {
                        "schema_name": self.schema_name,
                        "index_names": sorted(index_names),
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


@pytest.mark.skipif(
    os.getenv("TEST_DATABASE_URL") is None,
    reason="TEST_DATABASE_URL is required for Postgres migration smoke tests.",
)
def test_postgres_migrations_create_financial_workspace_tables_indexes_and_rls(
    migrated_postgres_schema: MigratedPostgresSchema,
) -> None:
    financial_tables = {
        "financial_institutions",
        "financial_accounts",
        "financial_reporting_periods",
        "financial_balances",
        "financial_cash_flows",
        "financial_obligations",
        "financial_covenants",
        "financial_source_rows",
        "financial_record_source_links",
        "financial_manual_edit_history",
        "financial_validation_issues",
    }

    assert migrated_postgres_schema.tables(financial_tables) == financial_tables
    financial_indexes = {
        "ix_financial_institutions_case_id",
        "uq_financial_institutions_dedupe_key",
        "ix_financial_accounts_case_id",
        "uq_financial_accounts_dedupe_key",
        "ix_financial_reporting_periods_case_id",
        "uq_financial_reporting_periods_dedupe_key",
        "ix_financial_balances_case_id",
        "uq_financial_balances_dedupe_key",
        "ix_financial_cash_flows_case_id",
        "uq_financial_cash_flows_dedupe_key",
        "ix_financial_obligations_case_id",
        "uq_financial_obligations_dedupe_key",
        "ix_financial_covenants_case_id",
        "ix_financial_covenants_obligation_id",
        "uq_financial_covenants_dedupe_key",
        "ix_financial_source_rows_case_id",
        "uq_financial_source_rows_extraction_row",
        "ix_financial_record_source_links_case_id",
        "uq_financial_record_source_links_field",
        "ix_financial_manual_edit_history_case_id",
        "uq_financial_validation_issues_current_natural_key",
    }
    assert migrated_postgres_schema.indexes(financial_indexes) == financial_indexes
    assert migrated_postgres_schema.policies(financial_tables) == {
        f"{table}_tenant_isolation" for table in financial_tables
    }
    assert migrated_postgres_schema.constraints(
        {
            "ck_financial_source_rows_row_index",
            "ck_financial_accounts_currency",
            "ck_financial_accounts_status",
            "ck_financial_reporting_periods_period_type",
            "ck_financial_balances_currency",
            "ck_financial_cash_flows_currency",
            "ck_financial_cash_flows_direction",
            "ck_financial_cash_flows_amount_positive",
            "ck_financial_cash_flows_category",
            "ck_financial_obligations_currency",
            "ck_financial_obligations_status",
            "ck_financial_covenants_operator",
            "ck_financial_covenants_compliance_status",
            "ck_financial_record_source_links_confidence",
            "ck_financial_record_source_links_record_table",
            "ck_financial_manual_edit_history_record_table",
            "ck_financial_validation_issues_severity",
            "ck_financial_validation_issues_status",
            "ck_financial_validation_issues_record_reference",
            "uq_risk_cases_id_organization_id",
            "uq_documents_id_organization_id_case_id",
            "uq_users_id_organization_id",
            "uq_financial_institutions_id_organization_id_case_id",
            "uq_financial_accounts_id_organization_id_case_id",
            "uq_financial_reporting_periods_id_organization_id_case_id",
            "uq_financial_balances_id_organization_id_case_id",
            "uq_financial_obligations_id_organization_id_case_id",
            "uq_financial_covenants_id_organization_id_case_id",
            "uq_financial_source_rows_id_organization_id_case_id",
        }
    ) == {
        "ck_financial_source_rows_row_index",
        "ck_financial_accounts_currency",
        "ck_financial_accounts_status",
        "ck_financial_reporting_periods_period_type",
        "ck_financial_balances_currency",
        "ck_financial_cash_flows_currency",
        "ck_financial_cash_flows_direction",
        "ck_financial_cash_flows_amount_positive",
        "ck_financial_cash_flows_category",
        "ck_financial_obligations_currency",
        "ck_financial_obligations_status",
        "ck_financial_covenants_operator",
        "ck_financial_covenants_compliance_status",
        "ck_financial_record_source_links_confidence",
        "ck_financial_record_source_links_record_table",
        "ck_financial_manual_edit_history_record_table",
        "ck_financial_validation_issues_severity",
        "ck_financial_validation_issues_status",
        "ck_financial_validation_issues_record_reference",
        "uq_risk_cases_id_organization_id",
        "uq_documents_id_organization_id_case_id",
        "uq_users_id_organization_id",
        "uq_financial_institutions_id_organization_id_case_id",
        "uq_financial_accounts_id_organization_id_case_id",
        "uq_financial_reporting_periods_id_organization_id_case_id",
        "uq_financial_balances_id_organization_id_case_id",
        "uq_financial_obligations_id_organization_id_case_id",
        "uq_financial_covenants_id_organization_id_case_id",
        "uq_financial_source_rows_id_organization_id_case_id",
    }


@pytest.mark.skipif(
    os.getenv("TEST_DATABASE_URL") is None,
    reason="TEST_DATABASE_URL is required for Postgres RLS tests.",
)
def test_postgres_financial_workspace_rls_isolates_tenant_rows(
    migrated_postgres_schema: MigratedPostgresSchema,
) -> None:
    case_id = uuid4()
    institution_id = uuid4()

    with migrated_postgres_schema.app_engine.connect() as connection:
        bypasses_rls = connection.scalar(
            text(
                """
                SELECT rolbypassrls
                FROM pg_roles
                WHERE rolname = current_user
                """
            )
        )
        if bypasses_rls:
            pytest.skip("Current TEST_DATABASE_URL role bypasses RLS.")
        connection.commit()

        with connection.begin():
            connection.execute(
                text("SELECT set_config('app.organization_id', :organization_id, true)"),
                {"organization_id": str(ORG_1)},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO organizations (id, name, created_at, updated_at)
                    VALUES (:organization_id, 'Tenant One', now(), now())
                    """
                ),
                {"organization_id": str(ORG_1)},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO risk_cases
                      (id, organization_id, title, case_type, status, created_at, updated_at)
                    VALUES
                      (
                        :case_id,
                        :organization_id,
                        'Tenant one financial case',
                        'vendor',
                        'active',
                        now(),
                        now()
                      )
                    """
                ),
                {"case_id": str(case_id), "organization_id": str(ORG_1)},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO financial_institutions
                      (id, organization_id, case_id, dedupe_key, name, created_at, updated_at)
                    VALUES
                      (
                        :institution_id,
                        :organization_id,
                        :case_id,
                        'test:institution:rls',
                        'Tenant One Bank',
                        now(),
                        now()
                      )
                    """
                ),
                {
                    "institution_id": str(institution_id),
                    "organization_id": str(ORG_1),
                    "case_id": str(case_id),
                },
            )

            visible_to_org_one = connection.scalar(
                text("SELECT count(*) FROM financial_institutions")
            )
            connection.execute(
                text("SELECT set_config('app.organization_id', :organization_id, true)"),
                {"organization_id": str(ORG_2)},
            )
            visible_to_org_two = connection.scalar(
                text("SELECT count(*) FROM financial_institutions")
            )

        assert visible_to_org_one == 1
        assert visible_to_org_two == 0
