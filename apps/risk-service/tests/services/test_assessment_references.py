from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import RiskAssessment, RiskAssessmentRun, RiskCase
from app.services.assessment_references import assessment_run_references
from tests.api.helpers import ORG_1, USER_1


def test_references_group_near_midnight_runs_by_utc_date(db_session: Session) -> None:
    if db_session.get_bind().dialect.name != "postgresql":
        pytest.skip("PostgreSQL session timezone regression")

    case = RiskCase(
        organization_id=ORG_1,
        title="Timezone reference case",
        case_type="borrower_risk",
        status="active",
        created_by=USER_1,
    )
    db_session.add(case)
    db_session.flush()
    assessment = RiskAssessment(
        organization_id=ORG_1,
        case_id=case.id,
        name="Credit review",
        assessment_type="borrower_risk",
        status="completed",
        created_by=USER_1,
    )
    db_session.add(assessment)
    db_session.flush()
    runs = [
        RiskAssessmentRun(
            id=uuid4(),
            organization_id=ORG_1,
            assessment_id=assessment.id,
            status="completed",
            created_at=created_at,
        )
        for created_at in (
            datetime(2026, 7, 14, 23, 30, tzinfo=UTC),
            datetime(2026, 7, 15, 0, 30, tzinfo=UTC),
        )
    ]
    db_session.add_all(runs)
    db_session.commit()
    db_session.execute(text("SET TIME ZONE 'Pacific/Honolulu'"))

    references = assessment_run_references(db_session, ORG_1, {run.id for run in runs})

    assert references == {
        runs[0].id: "Credit review 2026-07-14 run 1",
        runs[1].id: "Credit review 2026-07-15 run 1",
    }
