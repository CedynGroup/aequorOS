from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.models import RiskAssessment, RiskAssessmentRun


def assessment_run_references(
    db: Session, organization_id: UUID, run_ids: set[UUID]
) -> dict[UUID, str]:
    if not run_ids:
        return {}
    selected_rows = db.execute(
        select(
            RiskAssessmentRun.id,
            RiskAssessmentRun.created_at,
            RiskAssessment.name,
        )
        .join(RiskAssessment, RiskAssessment.id == RiskAssessmentRun.assessment_id)
        .where(
            RiskAssessmentRun.organization_id == organization_id,
            RiskAssessment.organization_id == organization_id,
            RiskAssessmentRun.id.in_(run_ids),
        )
    ).all()
    if not selected_rows:
        return {}

    groups = {(name, created_at.date()) for _, created_at, name in selected_rows}
    group_filters = []
    for name, run_date in groups:
        day_start = datetime.combine(run_date, time.min, tzinfo=UTC)
        group_filters.append(
            and_(
                RiskAssessment.name == name,
                RiskAssessmentRun.created_at >= day_start,
                RiskAssessmentRun.created_at < day_start + timedelta(days=1),
            )
        )

    ranked_runs = (
        select(
            RiskAssessmentRun.id.label("run_id"),
            RiskAssessment.name.label("assessment_name"),
            RiskAssessmentRun.created_at.label("created_at"),
            func.row_number()
            .over(
                partition_by=(
                    RiskAssessment.name,
                    func.date(RiskAssessmentRun.created_at),
                ),
                order_by=(RiskAssessmentRun.created_at, RiskAssessmentRun.id),
            )
            .label("ordinal"),
        )
        .join(RiskAssessment, RiskAssessment.id == RiskAssessmentRun.assessment_id)
        .where(
            RiskAssessmentRun.organization_id == organization_id,
            RiskAssessment.organization_id == organization_id,
            or_(*group_filters),
        )
        .subquery()
    )
    rows = db.execute(
        select(
            ranked_runs.c.run_id,
            ranked_runs.c.assessment_name,
            ranked_runs.c.created_at,
            ranked_runs.c.ordinal,
        ).where(ranked_runs.c.run_id.in_(run_ids))
    ).all()
    return {
        run_id: f"{name} {created_at.date().isoformat()} run {ordinal}"
        for run_id, name, created_at, ordinal in rows
    }
