"""SDI diagnostics endpoints (docs/sdi.md §11, §4.2).

Read-only tenant surfaces that make the SDI services reachable: per-module
data-quality readiness (what to feed to activate each module) and the simplified-
capital regulatory checks (paid-up-capital floor + statutory-reserve-fund
adequacy). Org-scoped; useful during onboarding for any institution.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.api.deps import DbSession, Tenant, TenantContext
from app.models import Bank, CanonicalPosition
from app.schemas.sdi import (
    CapitalCheckRead,
    DelinquencyBucketRead,
    LoanGradeBucketRead,
    ModuleReadinessRead,
    PortfolioAtRiskRead,
    ProvisionsHeldRead,
    RiskWeightBandRead,
    SdiCapitalAssuranceRead,
    SdiCapitalChecksRead,
    SdiCapitalHistoryPointRead,
    SdiCapitalSummaryRead,
    SdiCounterbalancingCapacityRead,
    SdiExposureRead,
    SdiFundingConcentrationRead,
    SdiFundingProviderRead,
    SdiLargeExposuresRead,
    SdiLiquidityPositionRead,
    SdiLiquidityRatioRead,
    SdiLiquidityReserveRead,
    SdiLoanClassificationRead,
    SdiMaturityBucketRead,
    SdiReadinessRead,
    SdiRwaRiskClassRead,
)
from app.services import banks as banks_service
from app.services import (
    loan_classification,
    sdi_capital,
    sdi_capital_assurance,
    sdi_capital_checks,
    sdi_readiness,
    sdi_views,
)

router = APIRouter(tags=["sdi-diagnostics"])


def _effective_as_of(db: DbSession, ctx: TenantContext, bank: Bank, requested: date | None) -> date:
    """Resolve the as-of date: the caller's, else the LATEST ingested data date
    (not ``date.today()`` — an S&L's core-banking feed lags the calendar, so a
    diagnostic keyed on today would read an empty future). Falls back to today
    when the bank has no canonical positions yet."""
    if requested is not None:
        return requested
    latest = db.scalar(
        select(func.max(CanonicalPosition.as_of_date)).where(
            CanonicalPosition.organization_id == ctx.organization_id,
            CanonicalPosition.bank_id == bank.id,
        )
    )
    return latest or date.today()


@router.get(
    "/banks/{bank_id}/sdi/readiness",
    response_model=SdiReadinessRead,
    operation_id="getSdiReadiness",
)
def get_sdi_readiness(
    bank_id: str,
    db: DbSession,
    ctx: Tenant,
    as_of: Annotated[date | None, Query()] = None,
) -> SdiReadinessRead:
    """Per-module readiness (READY/PARTIAL/BLOCKED) with the specific missing-data
    reasons — the onboarding data-quality view (docs/sdi.md §11)."""
    bank = banks_service.resolve_bank_reference(db, ctx, bank_id)
    when = _effective_as_of(db, ctx, bank, as_of)
    modules = sdi_readiness.assess_sdi_readiness(db, ctx, bank, when)
    return SdiReadinessRead(
        as_of=when.isoformat(),
        modules=[
            ModuleReadinessRead(module=m.module, status=m.status, reasons=m.reasons)
            for m in modules
        ],
    )


@router.get(
    "/banks/{bank_id}/sdi/capital-checks",
    response_model=SdiCapitalChecksRead,
    operation_id="getSdiCapitalChecks",
)
def get_sdi_capital_checks(
    bank_id: str,
    db: DbSession,
    ctx: Tenant,
    as_of: Annotated[date | None, Query()] = None,
) -> SdiCapitalChecksRead:
    """The simplified-capital checks: minimum paid-up capital + statutory-reserve-
    fund adequacy, with provenance + confirmation status (docs/sdi.md §4.2)."""
    bank = banks_service.resolve_bank_reference(db, ctx, bank_id)
    when = _effective_as_of(db, ctx, bank, as_of)
    results = [
        sdi_capital_checks.check_paid_up_capital(db, ctx, bank, when),
        sdi_capital_checks.check_statutory_reserve_fund(db, ctx, bank, when),
    ]
    return SdiCapitalChecksRead(
        as_of=when.isoformat(),
        checks=[
            CapitalCheckRead(
                check=c.check,
                compliant=c.compliant,
                actual_ghs=c.actual_ghs,
                required_ghs=c.required_ghs,
                detail=c.detail,
                source_citation=c.source_citation,
                confirmation_status=c.confirmation_status,
            )
            for c in results
        ],
    )


def _loan_classification_read(
    db: DbSession,
    ctx: Tenant,
    bank: Bank,
    as_of: date | None,
) -> SdiLoanClassificationRead:
    when = _effective_as_of(db, ctx, bank, as_of)
    report = loan_classification.classify_loan_book(db, ctx, bank, when)
    result = report.result
    return SdiLoanClassificationRead(
        as_of=report.as_of.isoformat(),
        institution_class=report.institution_class,
        loan_count=report.loan_count,
        total_exposure_ghs=result.total_exposure_ghs,
        npl_exposure_ghs=result.npl_exposure_ghs,
        npl_ratio=result.npl_ratio,
        total_provision_required_ghs=result.total_provision_required_ghs,
        stage_proxy_count=report.stage_proxy_count,
        dpd_covered_count=report.dpd_covered_count,
        dpd_covered_exposure_ghs=report.dpd_covered_exposure_ghs,
        unclassified_count=report.unclassified_count,
        buckets=[
            LoanGradeBucketRead(
                grade=b.grade,
                count=b.count,
                exposure_ghs=b.exposure_ghs,
                provision_required_ghs=b.provision_required_ghs,
                non_performing=b.non_performing,
            )
            for b in result.buckets
        ],
        delinquency_buckets=[
            DelinquencyBucketRead(
                code=bucket.code,
                label=bucket.label,
                count=bucket.count,
                exposure_ghs=bucket.exposure_ghs,
            )
            for bucket in report.delinquency_buckets
        ],
        portfolio_at_risk=[
            PortfolioAtRiskRead(
                code=metric.code,
                label=metric.label,
                exposure_ghs=metric.exposure_ghs,
                ratio=metric.ratio,
            )
            for metric in report.portfolio_at_risk
        ],
        pending_parameters=list(report.pending_parameters),
        provisions_held=(
            ProvisionsHeldRead(
                specific_ghs=report.provisions_held.specific_ghs,
                general_ghs=report.provisions_held.general_ghs,
                total_ghs=report.provisions_held.total_ghs,
                interest_in_suspense_ghs=report.provisions_held.interest_in_suspense_ghs,
                stated_loan_count=report.provisions_held.stated_loan_count,
            )
            if report.provisions_held is not None
            else None
        ),
        provision_coverage_pct=report.provision_coverage_pct,
    )


@router.get(
    "/banks/{bank_id}/loan-classification",
    response_model=SdiLoanClassificationRead,
    operation_id="getLoanClassification",
)
def get_loan_classification(
    bank_id: str,
    db: DbSession,
    ctx: Tenant,
    as_of: Annotated[date | None, Query()] = None,
) -> SdiLoanClassificationRead:
    """Class-aware loan classification, raw DPD, and portfolio-at-risk analytics."""
    bank = banks_service.resolve_bank_reference(db, ctx, bank_id)
    return _loan_classification_read(db, ctx, bank, as_of)


@router.get(
    "/banks/{bank_id}/sdi/loan-classification",
    response_model=SdiLoanClassificationRead,
    operation_id="getSdiLoanClassification",
)
def get_sdi_loan_classification(
    bank_id: str,
    db: DbSession,
    ctx: Tenant,
    as_of: Annotated[date | None, Query()] = None,
) -> SdiLoanClassificationRead:
    """Compatibility route for the class-aware loan classification response."""
    bank = banks_service.resolve_bank_reference(db, ctx, bank_id)
    return _loan_classification_read(db, ctx, bank, as_of)


@router.get(
    "/banks/{bank_id}/sdi/capital-summary",
    response_model=SdiCapitalSummaryRead,
    operation_id="getSdiCapitalSummary",
)
def get_sdi_capital_summary(
    bank_id: str,
    db: DbSession,
    ctx: Tenant,
    as_of: Annotated[date | None, Query()] = None,
) -> SdiCapitalSummaryRead:
    """The live s.29 capital-adequacy ratio: CAR = Net Own Funds ÷ Risk-Weighted
    Assets against the s.29 floor. Computed directly from canonical capital-structure
    + position data (the Basel live engine cannot serve an SDI); risk weights come
    from the simplified control-plane buckets, unconfirmed ones flagged (docs/sdi.md
    §4.2)."""
    bank = banks_service.resolve_bank_reference(db, ctx, bank_id)
    when = _effective_as_of(db, ctx, bank, as_of)
    summary = sdi_capital.compute_sdi_capital_summary(db, ctx, bank, when)
    return SdiCapitalSummaryRead(
        as_of=summary.as_of.isoformat(),
        net_own_funds_ghs=summary.net_own_funds_ghs,
        total_rwa_ghs=summary.total_rwa_ghs,
        car_pct=summary.car_pct,
        car_min_pct=summary.car_min_pct,
        status=summary.status,
        car_min_confirmation=summary.car_min_confirmation,
        computable=summary.computable,
        bands=[
            RiskWeightBandRead(
                bucket=b.bucket,
                weight_pct=b.weight_pct,
                exposure_ghs=b.exposure_ghs,
                rwa_ghs=b.rwa_ghs,
                confirmation_status=b.confirmation_status,
            )
            for b in summary.bands
        ],
        pending_parameters=list(summary.pending_parameters),
        composition_source=summary.composition_source,
        risk_classes=[
            SdiRwaRiskClassRead(
                risk_class=row.risk_class,
                in_scope=row.in_scope,
                measurement=row.measurement,
                rwa_ghs=row.rwa_ghs,
                note=row.note,
            )
            for row in summary.risk_classes
        ],
        rwa_scope_note=summary.rwa_scope_note,
    )


def _capital_history_point_read(
    item: sdi_capital_assurance.CapitalHistoryPoint,
) -> SdiCapitalHistoryPointRead:
    return SdiCapitalHistoryPointRead(
        as_of=item.as_of.isoformat(),
        net_own_funds_ghs=item.net_own_funds_ghs,
        total_rwa_ghs=item.total_rwa_ghs,
        car_pct=item.car_pct,
        capital_headroom_ghs=item.capital_headroom_ghs,
        npl_exposure_ghs=item.npl_exposure_ghs,
        npl_ratio=item.npl_ratio,
        required_provision_ghs=item.required_provision_ghs,
        actual_provision_ghs=item.actual_provision_ghs,
        provision_coverage_pct=item.provision_coverage_pct,
        assessment_status=item.assessment_status,
    )


@router.get(
    "/banks/{bank_id}/sdi/capital-assurance",
    response_model=SdiCapitalAssuranceRead,
    operation_id="getSdiCapitalAssurance",
)
def get_sdi_capital_assurance(
    bank_id: str,
    db: DbSession,
    ctx: Tenant,
    as_of: Annotated[date | None, Query()] = None,
    history_limit: Annotated[int, Query(ge=1, le=60)] = 12,
) -> SdiCapitalAssuranceRead:
    """Evidence-aware SDI capital history, reconciliations, and filing blockers."""
    bank = banks_service.resolve_bank_reference(db, ctx, bank_id)
    when = _effective_as_of(db, ctx, bank, as_of)
    assurance = sdi_capital_assurance.get_sdi_capital_assurance(
        db, ctx, bank, when, history_limit=history_limit
    )
    return SdiCapitalAssuranceRead(
        as_of=assurance.as_of.isoformat(),
        current=_capital_history_point_read(assurance.current),
        history=[_capital_history_point_read(item) for item in assurance.history],
        mapped_gl_capital_ghs=assurance.mapped_gl_capital_ghs,
        capital_to_gl_difference_ghs=assurance.capital_to_gl_difference_ghs,
        gl_reconciliation_status=assurance.gl_reconciliation_status,
        reserve_change_ghs=assurance.reserve_change_ghs,
        filing_status=assurance.filing_status,
        filing_blockers=assurance.filing_blockers,
    )


@router.get(
    "/banks/{bank_id}/sdi/liquidity-position",
    response_model=SdiLiquidityPositionRead,
    operation_id="getSdiLiquidityPosition",
)
def get_sdi_liquidity_position(
    bank_id: str,
    db: DbSession,
    ctx: Tenant,
    as_of: Annotated[date | None, Query()] = None,
) -> SdiLiquidityPositionRead:
    """Binding SDI LMTD liquidity measures: Table 1, reserves, and maturity ladder."""
    bank = banks_service.resolve_bank_reference(db, ctx, bank_id)
    when = _effective_as_of(db, ctx, bank, as_of)
    position = sdi_views.get_sdi_liquidity_position(db, ctx, bank, when)
    readiness = sdi_readiness.assess_sdi_readiness(db, ctx, bank, when)
    return SdiLiquidityPositionRead(
        as_of=position.as_of.isoformat(),
        ratios=[
            SdiLiquidityRatioRead(
                code=row.code,
                label=row.label,
                value_pct=row.value_pct,
                threshold_pct=row.threshold_pct,
                status=row.status,
                threshold_source=row.threshold_source,
            )
            for row in position.ratios
        ],
        reserves=[
            SdiLiquidityReserveRead(
                code=row.code,
                label=row.label,
                value_pct=row.value_pct,
                threshold_pct=row.threshold_pct,
                status=row.status,
                source_citation=row.source_citation,
                confirmation_status=row.confirmation_status,
            )
            for row in position.reserves
        ],
        maturity_ladder=[
            SdiMaturityBucketRead(
                code=row.code,
                label=row.label,
                net_mismatch_ghs=row.net_mismatch_ghs,
                cumulative_mismatch_ghs=row.cumulative_mismatch_ghs,
            )
            for row in position.maturity_ladder
        ],
        funding_concentration=SdiFundingConcentrationRead(
            total_deposits_ghs=position.funding_concentration.total_deposits_ghs,
            top_five_deposits_ghs=position.funding_concentration.top_five_deposits_ghs,
            top_five_pct=position.funding_concentration.top_five_pct,
            unattributed_deposits_ghs=position.funding_concentration.unattributed_deposits_ghs,
            providers=[
                SdiFundingProviderRead(
                    name=provider.name,
                    deposit_ghs=provider.deposit_ghs,
                    pct_total_deposits=provider.pct_total_deposits,
                    related=provider.related,
                )
                for provider in position.funding_concentration.providers
            ],
        ),
        counterbalancing_capacity=SdiCounterbalancingCapacityRead(
            gross_unencumbered_ghs=position.counterbalancing_capacity.gross_unencumbered_ghs,
            monetized_value_ghs=position.counterbalancing_capacity.monetized_value_ghs,
            bog_eligible_ghs=position.counterbalancing_capacity.bog_eligible_ghs,
            uncalibrated_asset_count=position.counterbalancing_capacity.uncalibrated_asset_count,
        ),
        readiness=[
            ModuleReadinessRead(module=row.module, status=row.status, reasons=row.reasons)
            for row in readiness
        ],
    )


@router.get(
    "/banks/{bank_id}/sdi/large-exposures",
    response_model=SdiLargeExposuresRead,
    operation_id="getSdiLargeExposures",
)
def get_sdi_large_exposures(
    bank_id: str,
    db: DbSession,
    ctx: Tenant,
    as_of: Annotated[date | None, Query()] = None,
) -> SdiLargeExposuresRead:
    """Connected-group exposure view against the SDI control-plane limits."""
    bank = banks_service.resolve_bank_reference(db, ctx, bank_id)
    when = _effective_as_of(db, ctx, bank, as_of)
    report = sdi_views.get_sdi_large_exposures(db, ctx, bank, when)
    return SdiLargeExposuresRead(
        as_of=report.as_of.isoformat(),
        net_own_funds_ghs=report.net_own_funds_ghs,
        exposures=[
            SdiExposureRead(
                counterparty_name=row.counterparty_name,
                connection=row.connection,
                exposure_ghs=row.exposure_ghs,
                pct_net_own_funds=row.pct_net_own_funds,
                single_obligor_limit_pct=row.single_obligor_limit_pct,
                large_exposure_limit_pct=row.large_exposure_limit_pct,
                status=row.status,
                exempt=row.exempt,
            )
            for row in report.exposures
        ],
        findings=report.findings,
    )
