from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.session import get_engine
from app.models import AuditEvent
from tests.api.factories import CaseFactory
from tests.api.helpers import headers


def test_audit_events_are_created(db_client: TestClient, db_settings: Settings) -> None:
    case_id = str(CaseFactory(db_client).create()["id"])
    db_client.patch(f"/api/v1/cases/{case_id}", headers=headers(), json={"status": "in_review"})
    assessment = db_client.post(
        "/api/v1/assessments",
        headers=headers(),
        json={"case_id": case_id, "assessment_type": "vendor_risk", "name": "Score"},
    )
    assert assessment.status_code == 201
    run = db_client.post(
        f"/api/v1/assessments/{assessment.json()['id']}/run",
        headers=headers(),
    )
    assert run.status_code == 200

    engine = get_engine(db_settings.database.database_url or "")
    with Session(engine) as session:
        events = list(session.scalars(select(AuditEvent)))
    event_types = [event.event_type for event in events]
    assert "case.created" in event_types
    assert "case.updated" in event_types
    assert "case.scored" in event_types
    scored_event = next(event for event in events if event.event_type == "case.scored")
    assert scored_event.details["risk_score"] == 20
    assert scored_event.details["scoring_version"] == "deterministic_v1"


def test_taxonomy_endpoints(db_client: TestClient) -> None:
    assert (
        "vendor_risk"
        in db_client.get("/api/v1/assessment-types", headers=headers()).json()["assessment_types"]
    )
    assert (
        "liquidity_risk"
        in db_client.get("/api/v1/risk-types", headers=headers()).json()["risk_types"]
    )
