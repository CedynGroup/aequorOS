"""operator_audit_log semantics: what gets logged.

Migrated PostgreSQL append-only enforcement belongs to tests/db.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import OperatorAuditLog
from tests.operator.conftest import operator_headers, provision_payload


def test_fleet_reads_are_audited_and_refused_inspection_leaves_no_access_event(
    operator_client: TestClient, operator_db: Session
) -> None:
    provision = operator_client.post(
        "/operator/v1/tenants", json=provision_payload(), headers=operator_headers()
    )
    organization_id = provision.json()["organization_id"]

    # Activity refuses a caller without an inspection; the successful fleet read is audited.
    operator_client.get(
        f"/operator/v1/tenants/{organization_id}/activity", headers=operator_headers()
    )
    operator_client.get("/operator/v1/data-engines", headers=operator_headers())
    actions = sorted(operator_db.scalars(select(OperatorAuditLog.action)).all())
    assert actions == ["data_engines.list", "tenants.provision"]

    # The cross-tenant enumeration IS logged, once per call.
    operator_client.get("/operator/v1/tenants", headers=operator_headers())
    operator_client.get("/operator/v1/tenants", headers=operator_headers())
    actions = sorted(operator_db.scalars(select(OperatorAuditLog.action)).all())
    assert actions == ["data_engines.list", "tenants.list", "tenants.list", "tenants.provision"]
