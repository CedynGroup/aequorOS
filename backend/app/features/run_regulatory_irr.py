from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from app.api.deps import DbSession, ScopedMutationTenant, Tenant
from app.core.authorization import Module, Permission, Sensitivity
from app.schemas.regulatory_irr import (
    IrrDashboardRead,
    IrrEarAnalysisRead,
    IrrScenarioBatchCreate,
)
from app.schemas.regulatory_irr_sf import (
    IrrbbSfAttemptsRead,
    IrrbbSfRead,
    IrrbbSfRunCreate,
)
from app.schemas.regulatory_liquidity import RegulatoryRunBatchRead, RegulatoryRunRead
from app.services import regulatory_irr, regulatory_irr_sf, scoped_authorization

router = APIRouter(tags=["regulatory-irr"])


@router.post(
    "/banks/{bank_id}/irr/run-all-scenarios",
    response_model=RegulatoryRunBatchRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="runAllIrrScenarios",
)
def run_all_irr_scenarios(
    bank_id: str,
    payload: IrrScenarioBatchCreate,
    db: DbSession,
    ctx: ScopedMutationTenant,
) -> RegulatoryRunBatchRead:
    scoped_authorization.require_bank_permission(
        db,
        ctx,
        bank_id,
        permission=Permission.RUN,
        module=Module.IRRBB,
        sensitivity=Sensitivity.CONFIDENTIAL,
        surface="irrbb_run_all_scenarios",
        denial_detail="Running IRRBB calculations requires an active scoped binding.",
    )
    return regulatory_irr.run_all_irr_scenarios(db, ctx, bank_id, payload)


@router.get(
    "/banks/{bank_id}/irr/ear-analysis",
    response_model=IrrEarAnalysisRead,
    operation_id="computeEarAnalysis",
)
def compute_ear_analysis(  # noqa: PLR0913 - the workbench seam names its full scope
    bank_id: str,
    db: DbSession,
    ctx: Tenant,
    reporting_period_id: Annotated[UUID, Query()],
    horizon_months: Annotated[int, Query()] = 12,
    delta_bp: Annotated[int, Query()] = 200,
) -> IrrEarAnalysisRead:
    """Pure desk analysis: generalized-horizon EaR on the canonical gap — writes nothing.

    The stored regulatory runs keep the 12-month ±200 bp figures; bounds are
    validated in the service (1..60 months, ±25..±500 bp on the 25 bp grid).
    """
    scoped_authorization.require_bank_permission(
        db,
        ctx,
        bank_id,
        permission=Permission.RUN,
        module=Module.IRRBB,
        sensitivity=Sensitivity.CONFIDENTIAL,
        surface="irrbb_ear_analysis",
        denial_detail="Running IRRBB analysis requires an active scoped binding.",
    )
    return regulatory_irr.compute_ear_analysis(
        db,
        ctx,
        bank_id,
        reporting_period_id,
        horizon_months=horizon_months,
        delta_bp=delta_bp,
    )


@router.get(
    "/banks/{bank_id}/irr/dashboard",
    response_model=IrrDashboardRead,
    operation_id="getIrrDashboard",
)
def get_irr_dashboard(
    bank_id: str,
    db: DbSession,
    ctx: Tenant,
    reporting_period_id: Annotated[UUID | None, Query()] = None,
) -> IrrDashboardRead:
    bank = scoped_authorization.require_bank_permission_prefetched(
        db,
        ctx,
        bank_id,
        permission=Permission.VIEW,
        module=Module.IRRBB,
        sensitivity=Sensitivity.AGGREGATED,
        surface="irrbb_dashboard",
        denial_detail="IRRBB access requires an active scoped binding.",
    )
    return regulatory_irr.get_irr_dashboard(
        db,
        ctx,
        bank.id,
        reporting_period_id,
        resolved_bank=bank,
    )


@router.post(
    "/banks/{bank_id}/irr/standardised-framework/runs",
    response_model=RegulatoryRunRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="runIrrbbStandardisedFramework",
)
def run_irrbb_standardised_framework(
    bank_id: str,
    payload: IrrbbSfRunCreate,
    db: DbSession,
    ctx: ScopedMutationTenant,
) -> RegulatoryRunRead:
    """Mint one immutable Standardised Framework run for a reporting date.

    Minting filing evidence is a CONFIDENTIAL run, like every other regulatory
    run — the aggregated read below is a different authority.
    """
    scoped_authorization.require_bank_permission(
        db,
        ctx,
        bank_id,
        permission=Permission.RUN,
        module=Module.IRRBB,
        sensitivity=Sensitivity.CONFIDENTIAL,
        surface="irrbb_sf_run",
        denial_detail="Running the IRRBB Standardised Framework requires an active "
        "scoped binding.",
    )
    return regulatory_irr_sf.run_standardised_framework(db, ctx, bank_id, payload)


@router.get(
    "/banks/{bank_id}/irr/standardised-framework",
    response_model=IrrbbSfRead,
    operation_id="getIrrbbStandardisedFramework",
)
def get_irrbb_standardised_framework(
    bank_id: str,
    db: DbSession,
    ctx: Tenant,
    reporting_period_id: Annotated[UUID, Query()],
) -> IrrbbSfRead:
    """The Standardised Framework result for one reporting date.

    Denial hides rather than announces: a principal with no IRRBB binding gets
    the same 404 as a bank that does not exist, so the route cannot be used to
    enumerate institutions.
    """
    bank = scoped_authorization.require_bank_permission_prefetched(
        db,
        ctx,
        bank_id,
        permission=Permission.VIEW,
        module=Module.IRRBB,
        sensitivity=Sensitivity.AGGREGATED,
        surface="irrbb_sf_view",
        denial_status=status.HTTP_404_NOT_FOUND,
        denial_detail="Bank not found.",
    )
    return regulatory_irr_sf.get_standardised_framework(
        db,
        ctx,
        bank.id,
        reporting_period_id,
        resolved_bank=bank,
    )


@router.get(
    "/banks/{bank_id}/irr/standardised-framework/attempts",
    response_model=IrrbbSfAttemptsRead,
    operation_id="listIrrbbStandardisedFrameworkAttempts",
)
def list_irrbb_standardised_framework_attempts(
    bank_id: str,
    db: DbSession,
    ctx: Tenant,
    reporting_period_id: Annotated[UUID, Query()],
) -> IrrbbSfAttemptsRead:
    """Was the framework tried at this reporting date, and what happened.

    A separate question from the result read, and the only one that can
    distinguish "nobody has run it" from "we ran it and it refused" — a refused
    run is a ``failed`` run and never reaches the result route. An empty
    history is an ANSWER here, never a 404.

    Same authority as the result read (IRRBB, aggregated, view), so it costs no
    new permission, and denial hides rather than announces for the same reason.
    """
    bank = scoped_authorization.require_bank_permission_prefetched(
        db,
        ctx,
        bank_id,
        permission=Permission.VIEW,
        module=Module.IRRBB,
        sensitivity=Sensitivity.AGGREGATED,
        surface="irrbb_sf_attempts",
        denial_status=status.HTTP_404_NOT_FOUND,
        denial_detail="Bank not found.",
    )
    return regulatory_irr_sf.get_standardised_framework_attempts(
        db,
        ctx,
        bank.id,
        reporting_period_id,
        resolved_bank=bank,
    )
