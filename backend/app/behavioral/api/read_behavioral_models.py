from __future__ import annotations

from datetime import date
from uuid import UUID

from fastapi import APIRouter

from app.api.deps import DbSession, ScopedMutationTenant, Tenant
from app.behavioral import service as behavioral_models
from app.behavioral.ml.config import ModelResult
from app.behavioral.models import BehavioralModelProposal
from app.behavioral.schemas import (
    BehavioralAccuracyRead,
    BehavioralApplyRead,
    BehavioralApplyRequest,
    BehavioralLiquidityRead,
    BehavioralLiquidityScenarioRead,
    BehavioralLiquiditySegmentRead,
    BehavioralModelRead,
    BehavioralModelSlug,
    BehavioralProductEstimate,
    BehavioralProposalAcknowledgement,
    BehavioralProposalListRead,
    BehavioralProposalRead,
    IncentivePoint,
)
from app.core.authorization import Module, Permission, Sensitivity
from app.identity import public as scoped_authorization
from app.liquidity.service import behavioral_liquidity

router = APIRouter(tags=["behavioral-models"])

VIEW_DENIAL_DETAIL = "Behavioral access requires an active scoped binding."
TRAIN_DENIAL_DETAIL = "Training behavioral models requires an active scoped binding."


def _to_read(result: ModelResult) -> BehavioralModelRead:
    return BehavioralModelRead(
        model_id=result.model_id,
        model_version=result.model_version,
        method=result.method,
        as_of_date=date.fromisoformat(result.as_of_date) if result.as_of_date else None,
        accuracy=BehavioralAccuracyRead(
            cv_rmse=result.accuracy.cv_rmse,
            cv_mae=result.accuracy.cv_mae,
            sample_count=result.accuracy.sample_count,
            month_coverage=result.accuracy.month_coverage,
            method=result.accuracy.method,
        ),
        products=[
            BehavioralProductEstimate(
                product_code=p.product_code,
                assumption_type=p.assumption_type,
                value=p.value,
                unit=p.unit,
                confidence=p.confidence,
                method=p.method,
                core_pct=p.extra.get("corePct"),
                incentive_curve=(
                    [
                        IncentivePoint(incentive_bps=pt["incentiveBps"], cpr=pt["cpr"])
                        for pt in p.extra["incentiveCurve"]
                    ]
                    if p.extra.get("incentiveCurve")
                    else None
                ),
            )
            for p in result.products
        ],
    )


@router.get(
    "/banks/{bank_id}/behavioral/liquidity",
    response_model=BehavioralLiquidityRead,
    operation_id="getBehavioralLiquidity",
)
def get_behavioral_liquidity(
    bank_id: str,
    db: DbSession,
    ctx: Tenant,
) -> BehavioralLiquidityRead:
    bank = scoped_authorization.require_bank_permission_prefetched(
        db,
        ctx,
        bank_id,
        permission=Permission.VIEW,
        module=Module.BEHAVIORAL,
        sensitivity=Sensitivity.AGGREGATED,
        surface="behavioral_liquidity",
        denial_detail=VIEW_DENIAL_DETAIL,
    )
    report = behavioral_liquidity.get_behavioral_liquidity_report(
        db, ctx, bank.id, resolved_bank=bank
    )
    return BehavioralLiquidityRead(
        as_of_date=report.as_of_date.isoformat() if report.as_of_date else "unavailable",
        segments=[BehavioralLiquiditySegmentRead(**item.__dict__) for item in report.segments],
        scenarios=[BehavioralLiquidityScenarioRead(**item.__dict__) for item in report.scenarios],
    )


def _proposal_to_read(proposal: BehavioralModelProposal) -> BehavioralProposalRead:
    return BehavioralProposalRead.model_validate(
        {
            "proposal_id": proposal.id,
            "bank_id": proposal.bank_id,
            "model": proposal.model,
            "model_version": proposal.model_version,
            "as_of_date": proposal.as_of_date,
            "values": proposal.values,
            "status": proposal.status,
            "proposed_by_user_id": proposal.proposed_by_user_id,
            "proposed_at": proposal.proposed_at,
            "reviewed_by_user_id": proposal.reviewed_by_user_id,
            "reviewed_at": proposal.reviewed_at,
            "ingestion_batch_id": proposal.ingestion_batch_id,
        }
    )


@router.get(
    "/banks/{bank_id}/behavioral/proposals",
    response_model=BehavioralProposalListRead,
    operation_id="listBehavioralModelProposals",
)
def list_behavioral_model_proposals(
    bank_id: str,
    db: DbSession,
    ctx: Tenant,
) -> BehavioralProposalListRead:
    return BehavioralProposalListRead(
        proposals=[
            _proposal_to_read(proposal)
            for proposal in behavioral_models.list_proposals(db, ctx, bank_id)
        ]
    )


@router.get(
    "/banks/{bank_id}/behavioral/{model}",
    response_model=BehavioralModelRead,
    operation_id="getBehavioralModel",
)
def get_behavioral_model(
    bank_id: str,
    model: BehavioralModelSlug,
    db: DbSession,
    ctx: Tenant,
) -> BehavioralModelRead:
    bank = scoped_authorization.require_bank_permission_prefetched(
        db,
        ctx,
        bank_id,
        permission=Permission.VIEW,
        module=Module.BEHAVIORAL,
        sensitivity=Sensitivity.AGGREGATED,
        surface="behavioral_model",
        denial_detail=VIEW_DENIAL_DETAIL,
    )
    return _to_read(behavioral_models.get_estimates(db, ctx, bank.id, model, resolved_bank=bank))


@router.post(
    "/banks/{bank_id}/behavioral/{model}/train",
    response_model=BehavioralModelRead,
    operation_id="trainBehavioralModel",
)
def train_behavioral_model(
    bank_id: str,
    model: BehavioralModelSlug,
    db: DbSession,
    ctx: ScopedMutationTenant,
) -> BehavioralModelRead:
    bank = scoped_authorization.require_bank_permission(
        db,
        ctx,
        bank_id,
        permission=Permission.RUN,
        module=Module.BEHAVIORAL,
        sensitivity=Sensitivity.CONFIDENTIAL,
        surface="behavioral_train",
        denial_detail=TRAIN_DENIAL_DETAIL,
    )
    return _to_read(
        behavioral_models.get_estimates(db, ctx, bank.id, model, refresh=True, resolved_bank=bank)
    )


@router.post(
    "/banks/{bank_id}/behavioral/{model}/apply",
    response_model=BehavioralProposalAcknowledgement,
    operation_id="applyBehavioralModel",
)
def apply_behavioral_model(
    bank_id: str,
    model: BehavioralModelSlug,
    request: BehavioralApplyRequest,
    db: DbSession,
    ctx: ScopedMutationTenant,
) -> BehavioralProposalAcknowledgement:
    rows: list[dict[str, object]] = [
        {
            "product_code": product.product_code,
            "value": product.value,
            "unit": product.unit or "",
            "confidence": product.confidence,
        }
        for product in request.products
    ]
    proposal = behavioral_models.propose_estimates(db, ctx, bank_id, model, rows)
    return BehavioralProposalAcknowledgement(proposal_id=proposal.id)


@router.post(
    "/banks/{bank_id}/behavioral/proposals/{proposal_id}/review",
    response_model=BehavioralApplyRead,
    operation_id="reviewBehavioralModelProposal",
)
def review_behavioral_model_proposal(
    bank_id: str,
    proposal_id: UUID,
    db: DbSession,
    ctx: ScopedMutationTenant,
) -> BehavioralApplyRead:
    summary = behavioral_models.review_estimates(db, ctx, bank_id, proposal_id)
    return BehavioralApplyRead(
        ingestion_batch_id=summary["ingestion_batch_id"],
        as_of_date=date.fromisoformat(summary["as_of_date"]),
        applied_rows=summary["applied_rows"],
        total_rows=summary["total_rows"],
    )
