from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import RiskAssessment, RiskAssessmentRun, RiskScore
from app.services.assessment_references import assessment_run_references
from app.services.reports import report_payload
from scripts.reset_demo import CASE_IDS, DEMO_ORG_ID, reset_demo
from tests.api.helpers import USER_1


def test_demo_reset_seeds_score_provenance_for_every_case(db_session: Session) -> None:
    reset_demo(db_session)

    assert db_session.scalar(
        select(func.count())
        .select_from(RiskAssessment)
        .where(RiskAssessment.organization_id == DEMO_ORG_ID)
    ) == len(CASE_IDS)
    scores = list(
        db_session.scalars(
            select(RiskScore)
            .where(RiskScore.organization_id == DEMO_ORG_ID)
            .order_by(RiskScore.case_id)
        )
    )
    assert {score.case_id for score in scores} == set(CASE_IDS.values())
    assert all(score.assessment_id is not None and score.run_id is not None for score in scores)
    run_ids = {score.run_id for score in scores if score.run_id is not None}
    assert db_session.scalar(
        select(func.count())
        .select_from(RiskAssessmentRun)
        .where(RiskAssessmentRun.organization_id == DEMO_ORG_ID)
    ) == len(CASE_IDS)
    references = assessment_run_references(db_session, DEMO_ORG_ID, run_ids)
    assert len(references) == len(CASE_IDS)
    assert all(reference.endswith("2026-07-01 run 1") for reference in references.values())
    report = report_payload(
        db_session,
        TenantContext(organization_id=DEMO_ORG_ID, actor_user_id=USER_1),
        CASE_IDS["completed"],
    )
    assert report.scores[0].run_reference in references.values()
    assert report.scores[0].input_hash
    assert report.scores[0].rule_results
