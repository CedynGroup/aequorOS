"""operator_audit_log semantics: what gets logged, and append-only enforcement.

The UPDATE/DELETE-blocking trigger is Postgres-only (SQLite has no trigger
guard and the hermetic suite builds its schema from the models, same stance
as migration 202607250027's attestation tables), so the enforcement test is
gated on TEST_DATABASE_URL exactly like the other Postgres migration tests.
"""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from alembic import command
from app.models import OperatorAuditLog
from tests.db.test_postgres_migrations import (
    alembic_config_for_app,
    clear_database_caches,
    postgres_schema_url,
)
from tests.operator.conftest import operator_headers, provision_payload


def test_reads_are_unlogged_except_tenants_list(
    operator_client: TestClient, operator_db: Session
) -> None:
    provision = operator_client.post(
        "/operator/v1/tenants", json=provision_payload(), headers=operator_headers()
    )
    organization_id = provision.json()["organization_id"]

    # Plain reads: activity + data-engines leave no audit rows.
    operator_client.get(
        f"/operator/v1/tenants/{organization_id}/activity", headers=operator_headers()
    )
    operator_client.get("/operator/v1/data-engines", headers=operator_headers())
    actions = sorted(
        operator_db.scalars(select(OperatorAuditLog.action)).all()
    )
    assert actions == ["tenants.provision"]

    # The cross-tenant enumeration IS logged, once per call.
    operator_client.get("/operator/v1/tenants", headers=operator_headers())
    operator_client.get("/operator/v1/tenants", headers=operator_headers())
    actions = sorted(operator_db.scalars(select(OperatorAuditLog.action)).all())
    assert actions == ["tenants.list", "tenants.list", "tenants.provision"]


@pytest.mark.skipif(
    os.getenv("TEST_DATABASE_URL") is None,
    reason="TEST_DATABASE_URL is required for the Postgres append-only trigger test.",
)
def test_operator_audit_log_update_and_delete_blocked_on_postgres(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_database_url = os.environ["TEST_DATABASE_URL"]
    if not make_url(test_database_url).drivername.startswith("postgresql"):
        pytest.skip("TEST_DATABASE_URL must point to Postgres.")

    schema_name = f"risk_service_operator_{uuid4().hex}"
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
            connection.execute(
                text(
                    """
                    INSERT INTO operator_audit_log
                        (id, operator_email, auth_mode, action, target_org, detail, created_at)
                    VALUES
                        (gen_random_uuid(), 'dev@aequoros.com', 'dev', 'tenants.provision',
                         NULL, '{}', now())
                    """
                )
            )
        # Two enforcement layers, either may fire first: the REVOKE (a
        # non-superuser role gets InsufficientPrivilege) or, for roles that
        # retain the privilege, the append-only trigger.
        blocked = r"append-only|permission denied"
        with pytest.raises(DBAPIError, match=blocked), app_engine.begin() as connection:
            connection.execute(
                text("UPDATE operator_audit_log SET action = 'tampered'")
            )
        with pytest.raises(DBAPIError, match=blocked), app_engine.begin() as connection:
            connection.execute(text("DELETE FROM operator_audit_log"))
    finally:
        clear_database_caches()
        app_engine.dispose()
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        admin_engine.dispose()
