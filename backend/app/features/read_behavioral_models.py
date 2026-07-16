from __future__ import annotations

from datetime import date

from fastapi import APIRouter

from app.api.deps import DbSession, MutationTenant, Tenant
from app.ml.behavioral.config import ModelResult
from app.schemas.behavioral_models import (
    BehavioralAccuracyRead,
    BehavioralApplyRead,
    BehavioralApplyRequest,
    BehavioralModelRead,
    BehavioralModelSlug,
    BehavioralProductEstimate,
    IncentivePoint,
)
from app.services import behavioral_models

router = APIRouter(tags=["behavioral-models"])


def _to_read(result: ModelResult) -> BehavioralModelRead:
    return BehavioralModelRead(
        model_id=result.model_id,
        model_version=result.model_version,
        method=result.method,
        as_of_date=date.fromisoformat(result.as_of_date) if result.as_of_date else None,
        accuracy=BehavioralAccuracyRead(
            cv_rmse=result.accuracy.cv_rmse, cv_mae=result.accuracy.cv_mae,
            sample_count=result.accuracy.sample_count,
            month_coverage=result.accuracy.month_coverage, method=result.accuracy.method,
        ),
        products=[
            BehavioralProductEstimate(
                product_code=p.product_code, assumption_type=p.assumption_type, value=p.value,
                unit=p.unit, confidence=p.confidence, method=p.method,
                core_pct=p.extra.get("corePct"),
                incentive_curve=(
                    [IncentivePoint(incentive_bps=pt["incentiveBps"], cpr=pt["cpr"])
                     for pt in p.extra["incentiveCurve"]]
                    if p.extra.get("incentiveCurve") else None
                ),
            )
            for p in result.products
        ],
    )


@router.get(
    "/banks/{bank_id}/behavioral/{model}",
    response_model=BehavioralModelRead,
    operation_id="getBehavioralModel",
)
def get_behavioral_model(
    bank_id, model: BehavioralModelSlug, db: DbSession, ctx: Tenant,
) -> BehavioralModelRead:
    return _to_read(behavioral_models.get_estimates(db, ctx, bank_id, model))


@router.post(
    "/banks/{bank_id}/behavioral/{model}/train",
    response_model=BehavioralModelRead,
    operation_id="trainBehavioralModel",
)
def train_behavioral_model(
    bank_id, model: BehavioralModelSlug, db: DbSession, ctx: MutationTenant,
) -> BehavioralModelRead:
    return _to_read(behavioral_models.get_estimates(db, ctx, bank_id, model, refresh=True))


@router.post(
    "/banks/{bank_id}/behavioral/{model}/apply",
    response_model=BehavioralApplyRead,
    operation_id="applyBehavioralModel",
)
def apply_behavioral_model(
    bank_id, model: BehavioralModelSlug, request: BehavioralApplyRequest,
    db: DbSession, ctx: MutationTenant,
) -> BehavioralApplyRead:
    rows = [
        {"product_code": p.product_code, "value": p.value, "unit": p.unit or "",
         "confidence": p.confidence}
        for p in request.products
    ]
    summary = behavioral_models.apply_estimates(db, ctx, bank_id, model, rows)
    return BehavioralApplyRead(
        ingestion_batch_id=summary["ingestion_batch_id"],
        as_of_date=date.fromisoformat(summary["as_of_date"]),
        applied_rows=summary["applied_rows"], total_rows=summary["total_rows"],
    )
