"""The BoG return-code recode must never be able to silently do nothing again.

Audit finding P0-18: migration 202608150013 rewrites FORCE-RLS reporting rows.
Run as the tenant-scoped app role it matched zero rows, reported success, and
alembic stamped it applied — the recode simply did not happen and nothing said
so. Its own docstring recorded the hazard and left the remediation to a human
remembering to re-run it under a BYPASSRLS role.

These tests pin the replacement: ``force_rls_suspended`` makes the rewrite
actually see every tenant, and where it cannot it raises instead of returning.

This module's other-suite cases live in ``test_bog_return_recode_migration.py``.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from alembic import command
from tests.support.database import install_migration_engines, managed_engine
from tests.support.postgres_migrations import (
    alembic_config_for_app,
    clear_database_caches,
    postgres_schema_url,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Connection
MIGRATION_DIR = Path(__file__).parents[2] / "alembic" / "versions"


def _load(filename: str, name: str):
    spec = importlib.util.spec_from_file_location(name, MIGRATION_DIR / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def recode_connection(request: pytest.FixtureRequest) -> Iterator[Connection]:
    engine = managed_engine(request, "sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE regulatory_packages ("
            "id TEXT PRIMARY KEY, organization_id TEXT NOT NULL, return_code TEXT NOT NULL, "
            "return_family TEXT NOT NULL, frequency TEXT NOT NULL, "
            "CONSTRAINT ck_regulatory_packages_return_family CHECK (return_family IN "
            "('liquidity', 'capital', 'irrbb', 'fx', 'icaap_stress', 'corporate', "
            "'large_exposures', 'dbk', 'stress')), "
            "CONSTRAINT ck_regulatory_packages_frequency CHECK (frequency IN "
            "('monthly', 'quarterly', 'semiannual', 'annual', 'daily')))"
        )
        for table in ("return_signing_policies", "return_signature_placements"):
            connection.exec_driver_sql(
                f"CREATE TABLE {table} (id TEXT PRIMARY KEY, "
                "organization_id TEXT NOT NULL, return_code TEXT NOT NULL)"
            )
        yield connection


def test_the_historical_recode_updates_all_tables_and_can_be_reversed(
    recode_connection: Connection,
) -> None:
    module = _load("202608150013_bog_prudential_returns.py", "bog_prudential_returns")
    tables = ("regulatory_packages", "return_signing_policies", "return_signature_placements")
    original: list[tuple[str, str, str]] = []
    recoded: list[tuple[str, str, str]] = []
    for org in ("OR-RECODE01", "OR-RECODE02"):
        for old, new, family in (
            ("BSD2", "CAR-RWA", "capital"),
            ("BSD3", "LCR-NSFR", "liquidity"),
            ("OTHER", "OTHER", "capital"),
        ):
            row_id = f"{org}-{old}"
            original.append((row_id, org, old))
            recoded.append((row_id, org, new))
            recode_connection.execute(
                text(
                    "INSERT INTO regulatory_packages VALUES (:id, :org, :code, :family, 'monthly')"
                ),
                {"id": row_id, "org": org, "code": old, "family": family},
            )
            for table in tables[1:]:
                recode_connection.execute(
                    text(f"INSERT INTO {table} VALUES (:id, :org, :code)"),
                    {"id": row_id, "org": org, "code": old},
                )

    with Operations.context(MigrationContext.configure(recode_connection)):
        module.upgrade()
        for table in tables:
            assert recode_connection.execute(
                text(f"SELECT id, organization_id, return_code FROM {table} ORDER BY id")
            ).all() == sorted(recoded)
        module.downgrade()
        for table in tables:
            assert recode_connection.execute(
                text(f"SELECT id, organization_id, return_code FROM {table} ORDER BY id")
            ).all() == sorted(original)


@pytest.mark.parametrize("has_legacy", [True, False], ids=["repair", "healthy"])
def test_the_corrective_migration_scopes_legacy_rows_by_family(
    recode_connection: Connection, has_legacy: bool
) -> None:
    """Official BSD2/BSD3 templates legitimately own those codes now.

    The corrective migration must not rename them back; only the pre-template
    reconstructions (families ``capital``/``liquidity``) are legacy.
    """
    historical = _load("202608150013_bog_prudential_returns.py", "bog_prudential_returns")
    module = _load("202608220029_verify_bog_return_recode.py", "verify_bog_return_recode")
    with Operations.context(MigrationContext.configure(recode_connection)):
        historical.upgrade()

    expected_packages: list[tuple[str, str, str, str, str]] = []
    expected_signing: list[tuple[str, str, str]] = []
    for org in ("OR-RECODE01", "OR-RECODE02"):
        for old, new, family in (
            ("BSD2", "CAR-RWA", "capital"),
            ("BSD3", "LCR-NSFR", "liquidity"),
        ):
            for package_family in (family, "bsd") if has_legacy else ("bsd",):
                row_id = f"{org}-{package_family}-{old}"
                recode_connection.execute(
                    text(
                        "INSERT INTO regulatory_packages "
                        "VALUES (:id, :org, :code, :family, 'monthly')"
                    ),
                    {"id": row_id, "org": org, "code": old, "family": package_family},
                )
                expected_packages.append(
                    (
                        row_id,
                        org,
                        old if package_family == "bsd" else new,
                        package_family,
                        "monthly",
                    )
                )
            for code in (old, new, "OTHER"):
                row_id = f"{org}-{old}-{code}"
                for table in ("return_signing_policies", "return_signature_placements"):
                    recode_connection.execute(
                        text(f"INSERT INTO {table} VALUES (:id, :org, :code)"),
                        {"id": row_id, "org": org, "code": code},
                    )
                expected_signing.append((row_id, org, new if has_legacy and code == old else code))

    with Operations.context(MigrationContext.configure(recode_connection)):
        for _ in range(2):
            module.upgrade()
            assert recode_connection.execute(
                text("SELECT * FROM regulatory_packages ORDER BY id")
            ).all() == sorted(expected_packages)
            for table in ("return_signing_policies", "return_signature_placements"):
                assert recode_connection.execute(
                    text(f"SELECT * FROM {table} ORDER BY id")
                ).all() == sorted(expected_signing)


@pytest.mark.skipif(
    os.getenv("TEST_DATABASE_URL") is None,
    reason="TEST_DATABASE_URL is required for the Postgres migration proof.",
)
def test_the_recode_applies_under_a_tenant_scoped_role_and_restores_force_rls(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    test_database_url = os.environ["TEST_DATABASE_URL"]
    schema_name = f"risk_service_recode_{uuid4().hex}"
    database_url = postgres_schema_url(test_database_url, schema_name)
    admin_engine = managed_engine(
        request, test_database_url, isolation_level="AUTOCOMMIT", schema=schema_name
    )
    schema_engine = managed_engine(request, database_url)
    monkeypatch.setenv("DATABASE_URL", database_url)
    clear_database_caches()

    with admin_engine.connect() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))
    try:
        with schema_engine.connect() as connection:
            bypasses = connection.scalar(
                text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user")
            )
        assert not bypasses, (
            "TEST_DATABASE_URL must use a role WITHOUT BYPASSRLS; a bypassing role "
            "cannot demonstrate the condition this test exists for."
        )

        # Runs 202608150013 for real. It must not raise.
        install_migration_engines(monkeypatch, schema_engine)
        command.upgrade(alembic_config_for_app(), "202608150013")

        with schema_engine.connect() as connection:
            forced = set(
                connection.execute(
                    text(
                        "SELECT c.relname FROM pg_class AS c "
                        "JOIN pg_namespace AS n ON n.oid = c.relnamespace "
                        "WHERE n.nspname = :schema AND c.relforcerowsecurity"
                    ),
                    {"schema": schema_name},
                ).scalars()
            )
        assert {"regulatory_packages", "return_signing_policies"} <= forced, (
            "FORCE ROW LEVEL SECURITY must be restored after the recode; the "
            f"schema reports forced tables {sorted(forced)}."
        )
    finally:
        clear_database_caches()
        schema_engine.dispose()
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        admin_engine.dispose()
