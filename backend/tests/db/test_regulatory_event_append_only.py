"""Postgres proof for immutable regulatory approval and submission evidence."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from alembic import command
from tests.db.test_postgres_migrations import (
    alembic_config_for_app,
    clear_database_caches,
    postgres_schema_url,
)


@pytest.mark.skipif(
    os.getenv("TEST_DATABASE_URL") is None,
    reason="TEST_DATABASE_URL is required for the Postgres append-only trigger test.",
)
def test_regulatory_approval_and_submission_events_are_append_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_database_url = os.environ["TEST_DATABASE_URL"]
    if not make_url(test_database_url).drivername.startswith("postgresql"):
        pytest.skip("TEST_DATABASE_URL must point to Postgres.")

    schema_name = f"risk_service_regulatory_evidence_{uuid4().hex}"
    database_url = postgres_schema_url(test_database_url, schema_name)
    admin_engine = create_engine(test_database_url, isolation_level="AUTOCOMMIT")
    app_engine = create_engine(database_url)
    monkeypatch.setenv("DATABASE_URL", database_url)
    clear_database_caches()

    with admin_engine.connect() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))
    try:
        command.upgrade(alembic_config_for_app(), "head")
        with app_engine.begin() as connection:
            _seed_regulatory_evidence(connection)
        for table, column in (
            ("regulatory_package_approvals", "action"),
            ("regulatory_submission_events", "event"),
        ):
            with pytest.raises(DBAPIError, match="append-only"), app_engine.begin() as connection:
                _set_org(connection)
                connection.execute(text(f"UPDATE {table} SET {column} = {column}"))
            with pytest.raises(DBAPIError, match="append-only"), app_engine.begin() as connection:
                _set_org(connection)
                connection.execute(text(f"DELETE FROM {table}"))

        # The canonical test fixture uses this transaction-local switch while
        # resetting disposable sample data. It cannot authorize an UPDATE.
        with app_engine.begin() as connection:
            _set_org(connection)
            connection.execute(
                text(
                    "SELECT set_config('app.aequoros_regulatory_event_test_reset', '1', true)"
                )
            )
            connection.execute(text("DELETE FROM regulatory_package_approvals"))
            connection.execute(text("DELETE FROM regulatory_submission_events"))
    finally:
        clear_database_caches()
        app_engine.dispose()
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        admin_engine.dispose()


def _set_org(connection) -> None:
    connection.execute(text("SELECT set_config('app.organization_id', 'OR-TEST0001', true)"))


def _seed_regulatory_evidence(connection) -> None:
    _set_org(connection)
    connection.execute(
        text(
            """
            -- ``created_at``/``updated_at`` are NOT NULL with no server default: the
            -- application supplies them through ``TimestampMixin``, which a raw INSERT
            -- bypasses. Omitting them here raised ``NotNullViolation`` in the fixture,
            -- so this module errored during setup and never reached an assertion —
            -- the append-only guarantee it exists to prove went unasserted from the
            -- day it was written (audit 2026-08-22 AUD-1). Every sibling INSERT below
            -- already passes ``now(), now()``; this one was the outlier.
            --
            -- ``ON CONFLICT DO NOTHING`` because the GLOBAL registries are seeded by
            -- their own migrations, which have already run against this schema. The
            -- fixture needs the row to EXIST, not to be the statement that created
            -- it — asserting ownership of a migration-seeded row is what turned the
            -- NOT NULL fix into a UniqueViolation.
            INSERT INTO jurisdictions
                (code, country_name, currency_code, currency_name, locale, central_bank_name,
                 regulator_short, sovereign_rating_issuer, submission_portal, timezone,
                 created_at, updated_at)
            VALUES
                ('GH', 'Ghana', 'GHS', 'Ghana Cedi', 'en-GH', 'Bank of Ghana', 'BoG',
                 'GHANA_SOVEREIGN', 'ORASS', 'Africa/Accra', now(), now())
            ON CONFLICT (code) DO NOTHING
            """
        )
    )
    connection.execute(
        text(
            """
            INSERT INTO institution_types
                (type_code, display_name, institution_class, return_family, capital_regime,
                 large_exposure_limit_pct, single_obligor_limit_pct, liquidity_binding,
                 default_modules, created_at, updated_at)
            VALUES
                ('universal_bank', 'Universal Bank', 'bank', 'bsd', 'crd', 20, 25, false,
                 '[]', now(), now())
            ON CONFLICT (type_code) DO NOTHING
            """
        )
    )
    connection.execute(
        text(
            """
            INSERT INTO organizations (id, name, created_at, updated_at)
            VALUES ('OR-TEST0001', 'Evidence Test Organization', now(), now())
            """
        )
    )
    connection.execute(
        text(
            """
            INSERT INTO banks
                (id, organization_id, name, short_name, currency, jurisdiction_code,
                 license_type, institution_type, created_at, updated_at)
            VALUES
                ('BK-TEST0001', 'OR-TEST0001', 'Evidence Test Bank', 'Evidence Bank', 'GHS',
                 'GH', 'universal_bank', 'universal_bank', now(), now())
            """
        )
    )
    connection.execute(
        text(
            """
            INSERT INTO regulatory_packages
                (id, organization_id, bank_id, return_family, return_code, reporting_date,
                 frequency, basis, status, version, snapshot, source_runs, generated_by,
                 generated_at, created_at, updated_at)
            VALUES
                (gen_random_uuid(), 'OR-TEST0001', 'BK-TEST0001', 'liquidity', 'LCR-NSFR',
                 DATE '2026-03-31', 'monthly', 'solo', 'generated', 1, '{}', '[]',
                 gen_random_uuid(), now(), now(), now())
            """
        )
    )
    connection.execute(
        text(
            """
            INSERT INTO regulatory_package_approvals
                (id, organization_id, package_id, action, actor_user_id, occurred_at, created_at)
            SELECT gen_random_uuid(), organization_id, id, 'requested',
                   gen_random_uuid(), now(), now()
            FROM regulatory_packages
            """
        )
    )
    connection.execute(
        text(
            """
            INSERT INTO regulatory_submission_events
                (id, organization_id, package_id, channel, event, detail, occurred_at, created_at)
            SELECT gen_random_uuid(), organization_id, id, 'manual', 'submitted', '{}', now(), now()
            FROM regulatory_packages
            """
        )
    )