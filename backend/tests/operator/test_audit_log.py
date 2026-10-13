"""operator_audit_log semantics: what gets logged.

Migrated PostgreSQL append-only enforcement belongs to tests/db.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import OperatorAuditLog
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
    actions = sorted(operator_db.scalars(select(OperatorAuditLog.action)).all())
    assert actions == ["tenants.provision"]

    # The cross-tenant enumeration IS logged, once per call.
    operator_client.get("/operator/v1/tenants", headers=operator_headers())
    operator_client.get("/operator/v1/tenants", headers=operator_headers())
    actions = sorted(operator_db.scalars(select(OperatorAuditLog.action)).all())
    assert actions == ["tenants.list", "tenants.list", "tenants.provision"]
