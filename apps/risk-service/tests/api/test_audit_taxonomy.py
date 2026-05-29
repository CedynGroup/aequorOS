from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_engine
from app.models import AuditEvent
from tests.api.factories import CaseFactory
from tests.api.helpers import headers


def test_audit_events_are_created(db_client: TestClient) -> None:
    case_id = str(CaseFactory(db_client).create()["id"])
    db_client.patch(f"/api/v1/cases/{case_id}", headers=headers(), json={"status": "in_review"})

    settings = get_settings()
    engine = get_engine(settings.database.database_url or "")
    with Session(engine) as session:
        event_types = list(session.scalars(select(AuditEvent.event_type)))
    assert "case.created" in event_types
    assert "case.updated" in event_types


def test_taxonomy_endpoints(db_client: TestClient) -> None:
    assert (
        "vendor_risk"
        in db_client.get("/api/v1/assessment-types", headers=headers()).json()["assessment_types"]
    )
    assert (
        "liquidity_risk"
        in db_client.get("/api/v1/risk-types", headers=headers()).json()["risk_types"]
    )
