from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID

from sqlalchemy import ColumnExpressionArgument, and_, func, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

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

    groups = {(name, _as_utc(created_at).date()) for _, created_at, name in selected_rows}
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
                    _utc_date(db, RiskAssessmentRun.created_at),
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
        run_id: f"{name} {_as_utc(created_at).date().isoformat()} run {ordinal}"
        for run_id, name, created_at, ordinal in rows
    }


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _utc_date(db: Session, column: ColumnExpressionArgument[datetime]) -> ColumnElement[date]:
    if db.get_bind().dialect.name == "postgresql":
        return func.date(func.timezone("UTC", column))
    return func.date(column)
