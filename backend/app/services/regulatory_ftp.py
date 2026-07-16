"""Regulatory FTP runs: curve / product / branch / NMD orchestration and dashboard.

Follows the immutable calculation-run lifecycle established by
``app.services.regulatory_capital`` and mirrored by ``app.services.regulatory_fx``:
runs commit ``queued`` and ``running`` before executing, persist the full
canonical input snapshot with a SHA-256 ``input_hash``, and record failures as
data (named error codes) rather than HTTP 500s. The arithmetic itself lives in
the pure engine at ``app.domain.ftp.engine``.

Every FTP run computes the complete matched-maturity funds-transfer-pricing
analysis — the transfer curve, product profitability (asset margin and deposit
funding credit), branch profitability ranking, and the non-maturity-deposit
core/volatile split. ``scenario_code`` tags which curve overlay the run
highlights: ``baseline`` prices against the base curve, while ``rates_up_200``
and ``funding_stress`` lift the transfer curve so the product margins reprice
under stress. The stored metrics and line items are the full analysis so any
run is a self-contained snapshot. FTP needs no capital denominator, so the hash
scopes reproducibility to the FTP facts and FTP parameters only.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.domain.ftp.engine import (
    BranchResult,
    CurvePoint,
    CurveResult,
    FtpBranch,
    FtpComputationError,
    FtpNmd,
    FtpProduct,
    MissingParameterError,
    NmdResult,
    ProductResult,
    branch_profitability,
    build_curve,
    classify_core_band,
    curve_within_premium_limits,
    nmd_split,
    product_profitability,
    shift_curve,
    validate_product_alignment,
)
from app.models import (
    Bank,
    BankFinancialFact,
    BankReportingPeriod,
    ParamCapitalThreshold,
    ParamStressShock,
    RegulatoryLineItem,
    RegulatoryMetricResult,
    RegulatoryRun,
    RegulatoryValidation,
)
from app.schemas.banks import BankRead, BankReportingPeriodRead
from app.schemas.regulatory_ftp import (
    FtpBranchRead,
    FtpCurvePointRead,
    FtpDashboardRead,
    FtpMetricsRead,
    FtpNmdSegmentRead,
    FtpProductRead,
    FtpScenarioBatchCreate,
    FtpTrendPointRead,
    FtpValidationRead,
)
from app.schemas.regulatory_liquidity import (
    RegulatoryRunBatchRead,
    RegulatoryRunRead,
)
from app.services.audit import record_event
from app.services.live_block import live_block
from app.services.live_types import (
    LiveModuleResult,
    findings_from_validations,
)
from app.services.params import get_active_params
from app.services.regulatory_liquidity import get_regulatory_run

ENGINE_VERSION = "regulatory-ftp-v1.0.0"
INPUT_SCHEMA_VERSION = "bank-facts-v2"
OUTPUT_SCHEMA_VERSION = "ftp-metrics-v1"
MODULE_FTP = "ftp"
BASELINE_SCENARIO = "baseline"
SCENARIO_RATES_UP = "rates_up_200"
SCENARIO_FUNDING_STRESS = "funding_stress"
FTP_RUN_SCENARIO_CODES = (BASELINE_SCENARIO, SCENARIO_RATES_UP, SCENARIO_FUNDING_STRESS)

TARGET_ROE = "ftp_target_roe_pct"
MIN_PRODUCT_MARGIN = "ftp_min_product_margin_pct"
LIQUIDITY_PREMIUM_MAX = "ftp_liquidity_premium_max_bps"
FUNDING_SPREAD_MAX = "ftp_funding_spread_max_bps"
NMD_CORE_MIN = "nmd_core_min_pct"
NMD_CORE_MAX = "nmd_core_max_pct"
_REQUIRED_THRESHOLDS = (
    TARGET_ROE,
    MIN_PRODUCT_MARGIN,
    LIQUIDITY_PREMIUM_MAX,
    FUNDING_SPREAD_MAX,
    NMD_CORE_MIN,
    NMD_CORE_MAX,
)

SHOCK_CURVE_SHIFT_BP = "curve_shift_bp"
SHOCK_FUNDING_ADD_BP = "funding_spread_add_bps"
_HUNDRED = Decimal("100")

# Only these fact groups participate in the FTP module; keeping the snapshot
# scoped to them makes the input hash insensitive to unrelated fact edits.
_FTP_FACT_GROUPS = ("ftp_curve_point", "ftp_product", "ftp_branch", "ftp_nmd")

_ZERO = Decimal("0")


class FtpRunError(Exception):
    """Domain input failure persisted onto the run instead of raising HTTP 500."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


@dataclass(frozen=True)
class _FtpParams:
    target_roe_pct: Decimal
    min_product_margin_pct: Decimal
    liquidity_premium_max_bps: Decimal
    funding_spread_max_bps: Decimal
    nmd_core_min_pct: Decimal
    nmd_core_max_pct: Decimal
    curve_shift_bp: Decimal
    funding_spread_add_bps: Decimal


@dataclass(frozen=True)
class _FtpAnalysis:
    scenario_code: str
    curve: CurveResult
    products: ProductResult
    branches: BranchResult
    nmd: NmdResult
    curve_shift_pct: Decimal
    liquidity_premium_max_bps: Decimal
    funding_spread_max_bps: Decimal


def run_all_ftp_scenarios(
    db: Session, ctx: TenantContext, bank_id: UUID, payload: FtpScenarioBatchCreate
) -> RegulatoryRunBatchRead:
    _require_actor(ctx)
    bank = _get_bank_or_404(db, ctx, bank_id)
    period = _get_period_or_404(db, ctx, bank, payload.reporting_period_id)
    runs = [
        _create_and_execute(db, ctx, bank, period, scenario_code)
        for scenario_code in FTP_RUN_SCENARIO_CODES
    ]
    return RegulatoryRunBatchRead(bank_id=bank.id, reporting_period_id=period.id, runs=runs)


def get_ftp_dashboard(
    db: Session, ctx: TenantContext, bank_id: UUID, reporting_period_id: UUID | None = None
) -> FtpDashboardRead:
    bank = _get_bank_or_404(db, ctx, bank_id)
    periods = _list_periods_ascending(db, ctx, bank)
    if not periods:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Reporting period not found."
        )
    period = (
        periods[-1]
        if reporting_period_id is None
        else _get_period_or_404(db, ctx, bank, reporting_period_id)
    )

    latest_run = _latest_succeeded_baseline_run(db, ctx, bank, period.id)
    if latest_run is not None:
        metrics = _metrics_from_run(latest_run)
        curve = _curve_from_run(latest_run)
        products = _products_from_run(latest_run)
        branches = _branches_from_run(latest_run)
        nmd_segments = _nmd_from_run(latest_run)
        validations = [
            FtpValidationRead(
                rule_code=item.rule_code,
                passed=item.passed,
                severity=item.severity,  # type: ignore[arg-type]
                message=item.message,
            )
            for item in _stored_validations(db, latest_run)
        ]
        stored = True
    else:
        analysis = _compute_inline_or_409(db, ctx, bank, period)
        metrics = _metrics_from_analysis(analysis)
        curve = _curve_from_analysis(analysis)
        products = _products_from_analysis(analysis)
        branches = _branches_from_analysis(analysis)
        nmd_segments = _nmd_from_analysis(analysis)
        validations = [
            FtpValidationRead(
                rule_code=rule_code,
                passed=passed,
                severity=severity,  # type: ignore[arg-type]
                message=message,
            )
            for rule_code, passed, severity, message in _validation_rows(analysis)
        ]
        stored = False

    return FtpDashboardRead(
        bank=BankRead.model_validate(bank, from_attributes=True),
        period=BankReportingPeriodRead.model_validate(period, from_attributes=True),
        stored=stored,
        latest_run_id=latest_run.id if latest_run is not None else None,
        metrics=metrics,
        curve=curve,
        products=products,
        branches=branches,
        nmd_segments=nmd_segments,
        trend=_build_trend(db, ctx, bank, periods),
        validations=validations,
        live=live_block(db, ctx, bank.id, period.id, MODULE_FTP),
    )


def _create_and_execute(
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    period: BankReportingPeriod,
    scenario_code: str,
) -> RegulatoryRunRead:
    facts = _load_facts(db, ctx, bank, period)
    active = _load_ftp_params_or_none(db, ctx, bank, period.period_end)
    snapshot = _build_snapshot(bank, period, scenario_code, facts, active)

    run = RegulatoryRun(
        organization_id=ctx.organization_id,
        bank_id=bank.id,
        reporting_period_id=period.id,
        module=MODULE_FTP,
        scenario_code=scenario_code,
        status="queued",
        engine_version=ENGINE_VERSION,
        input_schema_version=INPUT_SCHEMA_VERSION,
        output_schema_version=OUTPUT_SCHEMA_VERSION,
        input_hash=_snapshot_hash(snapshot),
        inputs=snapshot,
        metrics={},
        created_by=ctx.actor_user_id,
    )
    db.add(run)
    db.flush()
    record_event(
        db,
        ctx,
        event_type="regulatory_run.started",
        entity_type="regulatory_run",
        entity_id=run.id,
        details={
            "bank_id": str(bank.id),
            "reporting_period_id": str(period.id),
            "module": MODULE_FTP,
            "scenario_code": scenario_code,
            "input_hash": run.input_hash,
            "engine_version": ENGINE_VERSION,
        },
    )
    db.commit()

    run.status = "running"
    run.started_at = datetime.now(UTC)
    db.commit()

    run_id = run.id
    try:
        analysis = _run_analysis(scenario_code, facts, active)
        _persist_success(db, ctx, run, analysis)
    except FtpRunError as exc:
        _persist_failure(db, ctx, run_id, exc)
    except MissingParameterError as exc:
        _persist_failure(
            db,
            ctx,
            run_id,
            FtpRunError(
                "missing_parameter",
                f"No active FTP parameter covers '{exc.name}'.",
                {"parameter": exc.name},
            ),
        )
    except FtpComputationError as exc:
        _persist_failure(db, ctx, run_id, FtpRunError("calculation_error", str(exc), None))
    except HTTPException:
        raise
    except Exception:
        _persist_failure(
            db,
            ctx,
            run_id,
            FtpRunError(
                "calculation_error",
                "The FTP metrics could not be calculated.",
                {
                    "corrective_action": (
                        "Review the run inputs and retry. Contact support if it fails again."
                    )
                },
            ),
        )
    db.expire_all()
    return get_regulatory_run(db, ctx, bank.id, run_id)


def _run_analysis(
    scenario_code: str,
    facts: list[BankFinancialFact],
    active: _FtpParams | None,
) -> _FtpAnalysis:
    if not facts:
        raise FtpRunError(
            "financial_facts_missing",
            "The reporting period has no funds-transfer-pricing facts to analyze.",
            None,
        )
    if active is None:
        raise FtpRunError(
            "missing_parameter",
            "Required FTP parameters (margin floor, premium caps, NMD core band, or "
            "stress overlays) are not configured.",
            None,
        )
    curve_points = _curve_points_from_facts(facts)
    products = _products_from_facts(facts)
    branches = _branches_from_facts(facts)
    nmds = _nmds_from_facts(facts)
    if not curve_points or not products or not branches or not nmds:
        raise FtpRunError(
            "financial_facts_missing",
            "The reporting period is missing FTP curve, product, branch or NMD facts.",
            None,
        )

    base_curve = build_curve(curve_points)
    validate_product_alignment(products, base_curve)
    shift = _scenario_shift(scenario_code, active)
    curve = shift_curve(base_curve, shift)

    product_result = product_profitability(products, curve, active.min_product_margin_pct)
    branch_result = branch_profitability(
        branches,
        product_result.weighted_asset_yield_pct,
        product_result.weighted_funding_credit_pct,
    )
    nmd_result = nmd_split(nmds, curve, active.nmd_core_min_pct, active.nmd_core_max_pct)
    return _FtpAnalysis(
        scenario_code=scenario_code,
        curve=curve,
        products=product_result,
        branches=branch_result,
        nmd=nmd_result,
        curve_shift_pct=shift,
        liquidity_premium_max_bps=active.liquidity_premium_max_bps,
        funding_spread_max_bps=active.funding_spread_max_bps,
    )


def _scenario_shift(scenario_code: str, active: _FtpParams) -> Decimal:
    if scenario_code == SCENARIO_RATES_UP:
        return active.curve_shift_bp / _HUNDRED
    if scenario_code == SCENARIO_FUNDING_STRESS:
        return active.funding_spread_add_bps / _HUNDRED
    return _ZERO


def _persist_success(
    db: Session, ctx: TenantContext, run: RegulatoryRun, analysis: _FtpAnalysis
) -> None:
    run.metrics = _metrics_payload(analysis)

    products = analysis.products
    nmd = analysis.nmd
    core_status = classify_core_band(nmd.core_pct, nmd.core_min_pct, nmd.core_max_pct)
    metric_rows: list[tuple[str, Decimal, str, Decimal | None, str]] = [
        ("portfolio_nim_pct", products.portfolio_nim_pct, "pct", None, "na"),
        ("weighted_asset_yield_pct", products.weighted_asset_yield_pct, "pct", None, "na"),
        ("weighted_funding_credit_pct", products.weighted_funding_credit_pct, "pct", None, "na"),
        ("nmd_core_pct", nmd.core_pct, "pct", nmd.core_min_pct, core_status),
        (
            "total_branch_contribution_ghs",
            analysis.branches.total_contribution_ghs,
            "ghs",
            None,
            "na",
        ),
    ]
    for position, (code, value, unit, threshold_min, metric_status) in enumerate(
        metric_rows, start=1
    ):
        db.add(
            RegulatoryMetricResult(
                organization_id=run.organization_id,
                bank_id=run.bank_id,
                run_id=run.id,
                metric_code=code,
                metric_value=value,
                unit=unit,
                threshold_min=threshold_min,
                status=metric_status,
                position=position,
            )
        )

    line_items = (
        *analysis.curve.line_items,
        *analysis.products.line_items,
        *analysis.branches.line_items,
    )
    for position, item in enumerate(line_items, start=1):
        db.add(
            RegulatoryLineItem(
                organization_id=run.organization_id,
                bank_id=run.bank_id,
                run_id=run.id,
                section=item.section,
                line_code=item.line_code,
                description=item.description,
                exposure_amount=item.exposure_amount,
                rate_pct=item.rate_pct,
                weighted_amount=item.weighted_amount,
                position=position,
            )
        )

    for position, (rule_code, passed, severity, message) in enumerate(
        _validation_rows(analysis), start=1
    ):
        db.add(
            RegulatoryValidation(
                organization_id=run.organization_id,
                bank_id=run.bank_id,
                run_id=run.id,
                rule_code=rule_code,
                passed=passed,
                severity=severity,
                message=message,
                position=position,
            )
        )

    run.status = "succeeded"
    run.completed_at = datetime.now(UTC)
    record_event(
        db,
        ctx,
        event_type="regulatory_run.succeeded",
        entity_type="regulatory_run",
        entity_id=run.id,
        details={
            "input_hash": run.input_hash,
            "scenario_code": run.scenario_code,
            "portfolio_nim_pct": str(products.portfolio_nim_pct),
            "products_below_min_margin": products.products_below_min_margin,
        },
    )
    db.commit()


def _persist_failure(db: Session, ctx: TenantContext, run_id: UUID, error: FtpRunError) -> None:
    db.rollback()
    run = db.scalar(
        select(RegulatoryRun).where(
            RegulatoryRun.id == run_id,
            RegulatoryRun.organization_id == ctx.organization_id,
        )
    )
    if run is None:  # pragma: no cover - the queued row was committed earlier
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Regulatory run not found."
        )
    run.status = "failed"
    run.completed_at = datetime.now(UTC)
    run.error_code = error.code
    run.error_message = error.message
    run.error_details = error.details
    record_event(
        db,
        ctx,
        event_type="regulatory_run.failed",
        entity_type="regulatory_run",
        entity_id=run.id,
        details={
            "input_hash": run.input_hash,
            "scenario_code": run.scenario_code,
            "error_code": error.code,
        },
    )
    db.commit()


def _metrics_payload(analysis: _FtpAnalysis) -> dict[str, Any]:
    products = analysis.products
    nmd = analysis.nmd
    branches = analysis.branches
    return {
        "scenario_code": analysis.scenario_code,
        "curve_shift_pct": str(analysis.curve_shift_pct),
        "portfolio_nim_pct": str(products.portfolio_nim_pct),
        "weighted_asset_yield_pct": str(products.weighted_asset_yield_pct),
        "weighted_funding_credit_pct": str(products.weighted_funding_credit_pct),
        "total_balance_ghs": str(products.total_balance_ghs),
        "total_contribution_ghs": str(products.total_contribution_ghs),
        "products_below_min_margin": products.products_below_min_margin,
        "below_min_products": list(products.below_min_products),
        "total_products": len(products.products),
        "min_product_margin_pct": str(products.min_product_margin_pct),
        "total_branch_contribution_ghs": str(branches.total_contribution_ghs),
        "total_branch_deposits_ghs": str(branches.total_deposits_ghs),
        "total_branch_loans_ghs": str(branches.total_loans_ghs),
        "nmd_core_pct": str(nmd.core_pct),
        "nmd_volatile_pct": str(nmd.volatile_pct),
        "nmd_core_min_pct": str(nmd.core_min_pct),
        "nmd_core_max_pct": str(nmd.core_max_pct),
        "nmd_within_policy": nmd.within_policy,
        "nmd_blended_assigned_ftp_pct": str(nmd.blended_assigned_ftp_pct),
        "liquidity_premium_max_bps": str(analysis.liquidity_premium_max_bps),
        "funding_spread_max_bps": str(analysis.funding_spread_max_bps),
        "curve": [
            {
                "tenor_label": point.tenor_label,
                "tenor_years": str(point.tenor_years),
                "base_yield_pct": str(point.base_yield_pct),
                "liquidity_premium_bps": str(point.liquidity_premium_bps),
                "funding_spread_bps": str(point.funding_spread_bps),
                "ftp_rate_pct": str(point.ftp_rate_pct),
            }
            for point in analysis.curve.points
        ],
        "products": [
            {
                "product": product.product,
                "category": product.category,
                "balance_ghs": str(product.balance_ghs),
                "tenor_years": str(product.tenor_years),
                "customer_rate_pct": str(product.customer_rate_pct),
                "ftp_rate_pct": str(product.ftp_rate_pct),
                "operating_cost_pct": str(product.operating_cost_pct),
                "expected_credit_loss_pct": str(product.expected_credit_loss_pct),
                "capital_charge_pct": str(product.capital_charge_pct),
                "net_margin_pct": str(product.net_margin_pct),
                "contribution_ghs": str(product.contribution_ghs),
                "below_min_margin": product.below_min_margin,
            }
            for product in products.products
        ],
        "branches": [
            {
                "branch": branch.branch,
                "deposits_ghs": str(branch.deposits_ghs),
                "loans_ghs": str(branch.loans_ghs),
                "book_ghs": str(branch.book_ghs),
                "ftp_adjusted_nim_pct": str(branch.ftp_adjusted_nim_pct),
                "net_contribution_ghs": str(branch.net_contribution_ghs),
                "rank": branch.rank,
            }
            for branch in branches.branches
        ],
        "nmd_segments": [
            {
                "segment": segment.segment,
                "balance_ghs": str(segment.balance_ghs),
                "core_pct": str(segment.core_pct),
                "volatile_pct": str(segment.volatile_pct),
                "core_amount_ghs": str(segment.core_amount_ghs),
                "volatile_amount_ghs": str(segment.volatile_amount_ghs),
                "effective_duration_years": str(segment.effective_duration_years),
                "core_ftp_pct": str(segment.core_ftp_pct),
                "volatile_ftp_pct": str(segment.volatile_ftp_pct),
                "assigned_ftp_pct": str(segment.assigned_ftp_pct),
                "within_policy": segment.within_policy,
            }
            for segment in nmd.segments
        ],
    }


def _validation_rows(analysis: _FtpAnalysis) -> tuple[tuple[str, bool, str, str], ...]:
    products = analysis.products
    nmd = analysis.nmd
    curve = analysis.curve

    all_above = products.products_below_min_margin == 0
    if all_above:
        margin_message = (
            f"All {len(products.products)} products price at or above the "
            f"{_pct_text(products.min_product_margin_pct)}% minimum net FTP margin."
        )
    else:
        margin_message = (
            f"{products.products_below_min_margin} of {len(products.products)} products price "
            f"below the {_pct_text(products.min_product_margin_pct)}% minimum net FTP margin "
            f"({', '.join(products.below_min_products)})."
        )

    nmd_message = (
        f"The non-maturity-deposit core share of {_pct_text(nmd.core_pct)}% is "
        + ("within" if nmd.within_policy else "outside")
        + f" the {_pct_text(nmd.core_min_pct)}-{_pct_text(nmd.core_max_pct)}% policy band."
    )

    if curve.arithmetic_consistent:
        curve_message = (
            "Every transfer-curve point reconciles to base yield plus liquidity and funding premia."
        )
    else:
        curve_message = (
            "Transfer-curve points do not reconcile to base plus premia: "
            f"{', '.join(curve.inconsistent_labels)}."
        )

    premium_ok = curve_within_premium_limits(
        curve, analysis.liquidity_premium_max_bps, analysis.funding_spread_max_bps
    )
    premium_message = (
        "All transfer-curve points respect the "
        f"{_pct_text(analysis.liquidity_premium_max_bps)}bp liquidity-premium and "
        f"{_pct_text(analysis.funding_spread_max_bps)}bp funding-spread caps."
        if premium_ok
        else "A transfer-curve point exceeds the liquidity-premium or funding-spread cap."
    )

    return (
        ("all_products_above_min_margin", all_above, "warning", margin_message),
        ("nmd_core_within_policy", nmd.within_policy, "info", nmd_message),
        ("curve_arithmetic_consistent", curve.arithmetic_consistent, "error", curve_message),
        ("curve_within_premium_limits", premium_ok, "info", premium_message),
    )


def _metrics_read(
    products: ProductResult, nmd: NmdResult, total_branch_contribution: Decimal
) -> FtpMetricsRead:
    return FtpMetricsRead(
        portfolio_nim_pct=products.portfolio_nim_pct,
        weighted_asset_yield_pct=products.weighted_asset_yield_pct,
        weighted_funding_credit_pct=products.weighted_funding_credit_pct,
        total_balance_ghs=products.total_balance_ghs,
        total_contribution_ghs=products.total_contribution_ghs,
        products_below_min_margin=products.products_below_min_margin,
        total_products=len(products.products),
        min_product_margin_pct=products.min_product_margin_pct,
        total_branch_contribution_ghs=total_branch_contribution,
        nmd_core_pct=nmd.core_pct,
        nmd_core_status=classify_core_band(nmd.core_pct, nmd.core_min_pct, nmd.core_max_pct),
        nmd_core_min_pct=nmd.core_min_pct,
        nmd_core_max_pct=nmd.core_max_pct,
        blended_assigned_ftp_pct=nmd.blended_assigned_ftp_pct,
    )


def _metrics_from_analysis(analysis: _FtpAnalysis) -> FtpMetricsRead:
    return _metrics_read(analysis.products, analysis.nmd, analysis.branches.total_contribution_ghs)


def _curve_from_analysis(analysis: _FtpAnalysis) -> list[FtpCurvePointRead]:
    return [
        FtpCurvePointRead(
            tenor_label=point.tenor_label,
            tenor_years=point.tenor_years,
            base_yield_pct=point.base_yield_pct,
            liquidity_premium_bps=point.liquidity_premium_bps,
            funding_spread_bps=point.funding_spread_bps,
            ftp_rate_pct=point.ftp_rate_pct,
        )
        for point in analysis.curve.points
    ]


def _products_from_analysis(analysis: _FtpAnalysis) -> list[FtpProductRead]:
    return [
        FtpProductRead(
            product=product.product,
            category=product.category,
            balance_ghs=product.balance_ghs,
            tenor_years=product.tenor_years,
            customer_rate_pct=product.customer_rate_pct,
            ftp_rate_pct=product.ftp_rate_pct,
            operating_cost_pct=product.operating_cost_pct,
            expected_credit_loss_pct=product.expected_credit_loss_pct,
            capital_charge_pct=product.capital_charge_pct,
            net_margin_pct=product.net_margin_pct,
            contribution_ghs=product.contribution_ghs,
            below_min_margin=product.below_min_margin,
        )
        for product in analysis.products.products
    ]


def _branches_from_analysis(analysis: _FtpAnalysis) -> list[FtpBranchRead]:
    return [
        FtpBranchRead(
            branch=branch.branch,
            deposits_ghs=branch.deposits_ghs,
            loans_ghs=branch.loans_ghs,
            book_ghs=branch.book_ghs,
            ftp_adjusted_nim_pct=branch.ftp_adjusted_nim_pct,
            net_contribution_ghs=branch.net_contribution_ghs,
            rank=branch.rank,
        )
        for branch in analysis.branches.branches
    ]


def _nmd_from_analysis(analysis: _FtpAnalysis) -> list[FtpNmdSegmentRead]:
    return [
        FtpNmdSegmentRead(
            segment=segment.segment,
            balance_ghs=segment.balance_ghs,
            core_pct=segment.core_pct,
            volatile_pct=segment.volatile_pct,
            core_amount_ghs=segment.core_amount_ghs,
            volatile_amount_ghs=segment.volatile_amount_ghs,
            effective_duration_years=segment.effective_duration_years,
            core_ftp_pct=segment.core_ftp_pct,
            volatile_ftp_pct=segment.volatile_ftp_pct,
            assigned_ftp_pct=segment.assigned_ftp_pct,
            within_policy=segment.within_policy,
        )
        for segment in analysis.nmd.segments
    ]


def _metrics_from_run(run: RegulatoryRun) -> FtpMetricsRead:
    metrics = run.metrics
    core_pct = _decimal(metrics, "nmd_core_pct")
    core_min = _decimal(metrics, "nmd_core_min_pct")
    core_max = _decimal(metrics, "nmd_core_max_pct")
    return FtpMetricsRead(
        portfolio_nim_pct=_decimal(metrics, "portfolio_nim_pct"),
        weighted_asset_yield_pct=_decimal(metrics, "weighted_asset_yield_pct"),
        weighted_funding_credit_pct=_decimal(metrics, "weighted_funding_credit_pct"),
        total_balance_ghs=_decimal(metrics, "total_balance_ghs"),
        total_contribution_ghs=_decimal(metrics, "total_contribution_ghs"),
        products_below_min_margin=int(metrics["products_below_min_margin"]),
        total_products=int(metrics["total_products"]),
        min_product_margin_pct=_decimal(metrics, "min_product_margin_pct"),
        total_branch_contribution_ghs=_decimal(metrics, "total_branch_contribution_ghs"),
        nmd_core_pct=core_pct,
        nmd_core_status=classify_core_band(core_pct, core_min, core_max),
        nmd_core_min_pct=core_min,
        nmd_core_max_pct=core_max,
        blended_assigned_ftp_pct=_decimal(metrics, "nmd_blended_assigned_ftp_pct"),
    )


def _curve_from_run(run: RegulatoryRun) -> list[FtpCurvePointRead]:
    points: list[dict[str, Any]] = run.metrics.get("curve", [])
    return [
        FtpCurvePointRead(
            tenor_label=point["tenor_label"],
            tenor_years=Decimal(str(point["tenor_years"])),
            base_yield_pct=Decimal(str(point["base_yield_pct"])),
            liquidity_premium_bps=Decimal(str(point["liquidity_premium_bps"])),
            funding_spread_bps=Decimal(str(point["funding_spread_bps"])),
            ftp_rate_pct=Decimal(str(point["ftp_rate_pct"])),
        )
        for point in points
    ]


def _products_from_run(run: RegulatoryRun) -> list[FtpProductRead]:
    products: list[dict[str, Any]] = run.metrics.get("products", [])
    return [
        FtpProductRead(
            product=product["product"],
            category=product["category"],
            balance_ghs=Decimal(str(product["balance_ghs"])),
            tenor_years=Decimal(str(product["tenor_years"])),
            customer_rate_pct=Decimal(str(product["customer_rate_pct"])),
            ftp_rate_pct=Decimal(str(product["ftp_rate_pct"])),
            operating_cost_pct=Decimal(str(product["operating_cost_pct"])),
            expected_credit_loss_pct=Decimal(str(product["expected_credit_loss_pct"])),
            capital_charge_pct=Decimal(str(product["capital_charge_pct"])),
            net_margin_pct=Decimal(str(product["net_margin_pct"])),
            contribution_ghs=Decimal(str(product["contribution_ghs"])),
            below_min_margin=bool(product["below_min_margin"]),
        )
        for product in products
    ]


def _branches_from_run(run: RegulatoryRun) -> list[FtpBranchRead]:
    branches: list[dict[str, Any]] = run.metrics.get("branches", [])
    return [
        FtpBranchRead(
            branch=branch["branch"],
            deposits_ghs=Decimal(str(branch["deposits_ghs"])),
            loans_ghs=Decimal(str(branch["loans_ghs"])),
            book_ghs=Decimal(str(branch["book_ghs"])),
            ftp_adjusted_nim_pct=Decimal(str(branch["ftp_adjusted_nim_pct"])),
            net_contribution_ghs=Decimal(str(branch["net_contribution_ghs"])),
            rank=int(branch["rank"]),
        )
        for branch in branches
    ]


def _nmd_from_run(run: RegulatoryRun) -> list[FtpNmdSegmentRead]:
    segments: list[dict[str, Any]] = run.metrics.get("nmd_segments", [])
    return [
        FtpNmdSegmentRead(
            segment=segment["segment"],
            balance_ghs=Decimal(str(segment["balance_ghs"])),
            core_pct=Decimal(str(segment["core_pct"])),
            volatile_pct=Decimal(str(segment["volatile_pct"])),
            core_amount_ghs=Decimal(str(segment["core_amount_ghs"])),
            volatile_amount_ghs=Decimal(str(segment["volatile_amount_ghs"])),
            effective_duration_years=Decimal(str(segment["effective_duration_years"])),
            core_ftp_pct=Decimal(str(segment["core_ftp_pct"])),
            volatile_ftp_pct=Decimal(str(segment["volatile_ftp_pct"])),
            assigned_ftp_pct=Decimal(str(segment["assigned_ftp_pct"])),
            within_policy=bool(segment["within_policy"]),
        )
        for segment in segments
    ]


def _build_trend(
    db: Session, ctx: TenantContext, bank: Bank, periods: list[BankReportingPeriod]
) -> list[FtpTrendPointRead]:
    points: list[FtpTrendPointRead] = []
    for period in periods:
        run = _latest_succeeded_baseline_run(db, ctx, bank, period.id)
        if run is not None:
            metrics = run.metrics
            points.append(
                FtpTrendPointRead(
                    reporting_period_id=period.id,
                    label=period.label,
                    period_end=period.period_end,
                    portfolio_nim_pct=_decimal(metrics, "portfolio_nim_pct"),
                    products_below_min_margin=int(metrics["products_below_min_margin"]),
                    nmd_core_pct=_decimal(metrics, "nmd_core_pct"),
                    stored=True,
                )
            )
            continue
        try:
            analysis = _compute_inline(db, ctx, bank, period)
        except (MissingParameterError, FtpComputationError, FtpRunError):
            continue
        points.append(
            FtpTrendPointRead(
                reporting_period_id=period.id,
                label=period.label,
                period_end=period.period_end,
                portfolio_nim_pct=analysis.products.portfolio_nim_pct,
                products_below_min_margin=analysis.products.products_below_min_margin,
                nmd_core_pct=analysis.nmd.core_pct,
                stored=False,
            )
        )
    return points


def _compute_inline(
    db: Session, ctx: TenantContext, bank: Bank, period: BankReportingPeriod
) -> _FtpAnalysis:
    facts = _load_facts(db, ctx, bank, period)
    active = _load_ftp_params_or_none(db, ctx, bank, period.period_end)
    return _run_analysis(BASELINE_SCENARIO, facts, active)


def _compute_inline_or_409(
    db: Session, ctx: TenantContext, bank: Bank, period: BankReportingPeriod
) -> _FtpAnalysis:
    try:
        return _compute_inline(db, ctx, bank, period)
    except MissingParameterError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error_code": "missing_parameter", "message": str(exc), "parameter": exc.name},
        ) from exc
    except FtpRunError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error_code": exc.code, "message": exc.message},
        ) from exc
    except FtpComputationError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error_code": "calculation_error", "message": str(exc)},
        ) from exc


def current_input_hash(
    db: Session, ctx: TenantContext, bank: Bank, period: BankReportingPeriod
) -> str | None:
    """The baseline input hash of the current canonical state for this period,
    built with the same snapshot + hash as the immutable baseline run."""
    facts = _load_facts(db, ctx, bank, period)
    if not facts:
        return None
    active = _load_ftp_params_or_none(db, ctx, bank, period.period_end)
    snapshot = _build_snapshot(bank, period, BASELINE_SCENARIO, facts, active)
    return _snapshot_hash(snapshot)


def compute_live(
    db: Session, ctx: TenantContext, bank: Bank, period: BankReportingPeriod
) -> LiveModuleResult:
    """Cheap baseline live view — reuses the dashboard's unstored-branch path
    and creates no RegulatoryRun."""
    analysis = _compute_inline(db, ctx, bank, period)
    metrics = _metrics_from_analysis(analysis)
    live_metrics = {
        "portfolio_nim_pct": str(metrics.portfolio_nim_pct),
        "nmd_core_pct": str(metrics.nmd_core_pct),
        "products_below_min_margin": str(metrics.products_below_min_margin),
        "total_balance_ghs": str(metrics.total_balance_ghs),
    }
    status = metrics.nmd_core_status
    findings = findings_from_validations(_validation_rows(analysis), status)
    return LiveModuleResult(
        metrics=live_metrics,
        status=status,
        input_hash=current_input_hash(db, ctx, bank, period),
        findings=findings,
    )


def _curve_points_from_facts(facts: list[BankFinancialFact]) -> list[CurvePoint]:
    points: list[CurvePoint] = []
    for fact in facts:
        if fact.fact_group != "ftp_curve_point":
            continue
        attributes = fact.attributes
        points.append(
            CurvePoint(
                tenor_label=attributes["tenor_label"],
                tenor_years=Decimal(str(attributes["tenor_years"])),
                base_yield_pct=Decimal(str(attributes["base_yield_pct"])),
                liquidity_premium_bps=Decimal(str(attributes["liquidity_premium_bps"])),
                funding_spread_bps=Decimal(str(attributes["funding_spread_bps"])),
                ftp_rate_pct=Decimal(str(attributes["ftp_rate_pct"])),
            )
        )
    return points


def _products_from_facts(facts: list[BankFinancialFact]) -> list[FtpProduct]:
    products: list[FtpProduct] = []
    for fact in facts:
        if fact.fact_group != "ftp_product":
            continue
        attributes = fact.attributes
        products.append(
            FtpProduct(
                product=attributes["product"],
                category=attributes["category"],
                balance_ghs=Decimal(str(fact.amount)),
                tenor_years=Decimal(str(attributes["tenor_years"])),
                customer_rate_pct=Decimal(str(attributes["customer_rate_pct"])),
                ftp_rate_pct=Decimal(str(attributes["ftp_rate_pct"])),
                operating_cost_pct=Decimal(str(attributes["operating_cost_pct"])),
                expected_credit_loss_pct=Decimal(str(attributes["expected_credit_loss_pct"])),
                capital_charge_pct=Decimal(str(attributes["capital_charge_pct"])),
            )
        )
    return products


def _branches_from_facts(facts: list[BankFinancialFact]) -> list[FtpBranch]:
    branches: list[FtpBranch] = []
    for fact in facts:
        if fact.fact_group != "ftp_branch":
            continue
        attributes = fact.attributes
        branches.append(
            FtpBranch(
                branch=attributes["branch"],
                deposits_ghs=Decimal(str(fact.amount)),
                loans_ghs=Decimal(str(attributes["loans_ghs"])),
            )
        )
    return branches


def _nmds_from_facts(facts: list[BankFinancialFact]) -> list[FtpNmd]:
    nmds: list[FtpNmd] = []
    for fact in facts:
        if fact.fact_group != "ftp_nmd":
            continue
        attributes = fact.attributes
        nmds.append(
            FtpNmd(
                segment=attributes["segment"],
                balance_ghs=Decimal(str(fact.amount)),
                core_pct=Decimal(str(attributes["core_pct"])),
                volatile_pct=Decimal(str(attributes["volatile_pct"])),
                effective_duration_years=Decimal(str(attributes["effective_duration_years"])),
            )
        )
    return nmds


def _load_facts(
    db: Session, ctx: TenantContext, bank: Bank, period: BankReportingPeriod
) -> list[BankFinancialFact]:
    return list(
        db.scalars(
            select(BankFinancialFact)
            .where(
                BankFinancialFact.organization_id == ctx.organization_id,
                BankFinancialFact.bank_id == bank.id,
                BankFinancialFact.reporting_period_id == period.id,
                BankFinancialFact.fact_group.in_(_FTP_FACT_GROUPS),
            )
            .order_by(BankFinancialFact.fact_group, BankFinancialFact.category)
        )
    )


def _load_ftp_params_or_none(
    db: Session, ctx: TenantContext, bank: Bank, as_of: date
) -> _FtpParams | None:
    thresholds = {
        row.threshold_code: Decimal(str(row.value_pct))
        for row in get_active_params(
            db, ctx.organization_id, bank.jurisdiction_code, ParamCapitalThreshold, as_of
        )
    }
    if any(code not in thresholds for code in _REQUIRED_THRESHOLDS):
        return None

    scenario_shocks: dict[str, dict[str, Decimal]] = {}
    for row in get_active_params(
        db, ctx.organization_id, bank.jurisdiction_code, ParamStressShock, as_of
    ):
        if row.module != MODULE_FTP:
            continue
        scenario_shocks.setdefault(row.scenario_code, {})[row.shock_key] = Decimal(
            str(row.shock_value)
        )

    rates_up = scenario_shocks.get(SCENARIO_RATES_UP, {})
    funding = scenario_shocks.get(SCENARIO_FUNDING_STRESS, {})
    if SHOCK_CURVE_SHIFT_BP not in rates_up or SHOCK_FUNDING_ADD_BP not in funding:
        return None

    return _FtpParams(
        target_roe_pct=thresholds[TARGET_ROE],
        min_product_margin_pct=thresholds[MIN_PRODUCT_MARGIN],
        liquidity_premium_max_bps=thresholds[LIQUIDITY_PREMIUM_MAX],
        funding_spread_max_bps=thresholds[FUNDING_SPREAD_MAX],
        nmd_core_min_pct=thresholds[NMD_CORE_MIN],
        nmd_core_max_pct=thresholds[NMD_CORE_MAX],
        curve_shift_bp=rates_up[SHOCK_CURVE_SHIFT_BP],
        funding_spread_add_bps=funding[SHOCK_FUNDING_ADD_BP],
    )


def _build_snapshot(
    bank: Bank,
    period: BankReportingPeriod,
    scenario_code: str,
    facts: list[BankFinancialFact],
    active: _FtpParams | None,
) -> dict[str, Any]:
    return {
        "schema_version": INPUT_SCHEMA_VERSION,
        "module": MODULE_FTP,
        "scenario_code": scenario_code,
        "bank_id": str(bank.id),
        "currency": bank.currency,
        "jurisdiction_code": bank.jurisdiction_code,
        "reporting_period": {
            "id": str(period.id),
            "label": period.label,
            "period_start": period.period_start.isoformat(),
            "period_end": period.period_end.isoformat(),
        },
        "as_of_date": period.period_end.isoformat(),
        "facts": sorted(
            (
                {
                    "fact_group": fact.fact_group,
                    "category": fact.category,
                    "amount": str(fact.amount),
                    "attributes": _sorted_attributes(fact.attributes),
                }
                for fact in facts
            ),
            key=lambda entry: json.dumps(entry, sort_keys=True),
        ),
        "parameters": _snapshot_parameters(active),
    }


def _snapshot_parameters(active: _FtpParams | None) -> dict[str, Any]:
    if active is None:
        return {"thresholds": {}, "stress_overlays_bps": {}}
    return {
        "thresholds": {
            TARGET_ROE: str(active.target_roe_pct),
            MIN_PRODUCT_MARGIN: str(active.min_product_margin_pct),
            LIQUIDITY_PREMIUM_MAX: str(active.liquidity_premium_max_bps),
            FUNDING_SPREAD_MAX: str(active.funding_spread_max_bps),
            NMD_CORE_MIN: str(active.nmd_core_min_pct),
            NMD_CORE_MAX: str(active.nmd_core_max_pct),
        },
        "stress_overlays_bps": {
            SCENARIO_RATES_UP: str(active.curve_shift_bp),
            SCENARIO_FUNDING_STRESS: str(active.funding_spread_add_bps),
        },
    }


def _sorted_attributes(attributes: dict[str, Any]) -> dict[str, Any]:
    return {key: attributes[key] for key in sorted(attributes)}


def _snapshot_hash(snapshot: dict[str, Any]) -> str:
    payload = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def _decimal(metrics: dict[str, Any], key: str) -> Decimal:
    return Decimal(str(metrics[key]))


def _pct_text(value: Decimal) -> str:
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def _stored_validations(db: Session, run: RegulatoryRun) -> list[RegulatoryValidation]:
    return list(
        db.scalars(
            select(RegulatoryValidation)
            .where(
                RegulatoryValidation.run_id == run.id,
                RegulatoryValidation.organization_id == run.organization_id,
                RegulatoryValidation.bank_id == run.bank_id,
            )
            .order_by(RegulatoryValidation.position)
        )
    )


def _latest_succeeded_baseline_run(
    db: Session, ctx: TenantContext, bank: Bank, reporting_period_id: UUID
) -> RegulatoryRun | None:
    return db.scalar(
        select(RegulatoryRun)
        .where(
            RegulatoryRun.organization_id == ctx.organization_id,
            RegulatoryRun.bank_id == bank.id,
            RegulatoryRun.reporting_period_id == reporting_period_id,
            RegulatoryRun.module == MODULE_FTP,
            RegulatoryRun.scenario_code == BASELINE_SCENARIO,
            RegulatoryRun.status == "succeeded",
        )
        .order_by(RegulatoryRun.created_at.desc(), RegulatoryRun.id.desc())
        .limit(1)
    )


def _list_periods_ascending(
    db: Session, ctx: TenantContext, bank: Bank
) -> list[BankReportingPeriod]:
    return list(
        db.scalars(
            select(BankReportingPeriod)
            .where(
                BankReportingPeriod.organization_id == ctx.organization_id,
                BankReportingPeriod.bank_id == bank.id,
            )
            .order_by(BankReportingPeriod.period_end)
        )
    )


def _get_bank_or_404(db: Session, ctx: TenantContext, bank_id: UUID) -> Bank:
    bank = db.scalar(
        select(Bank).where(Bank.id == bank_id, Bank.organization_id == ctx.organization_id)
    )
    if bank is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bank not found.")
    return bank


def _get_period_or_404(
    db: Session, ctx: TenantContext, bank: Bank, period_id: UUID
) -> BankReportingPeriod:
    period = db.scalar(
        select(BankReportingPeriod).where(
            BankReportingPeriod.id == period_id,
            BankReportingPeriod.organization_id == ctx.organization_id,
            BankReportingPeriod.bank_id == bank.id,
        )
    )
    if period is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Reporting period not found."
        )
    return period


def _require_actor(ctx: TenantContext) -> None:
    if ctx.actor_user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="X-User-Id header is required."
        )
