"""Regulatory FX runs: net-open-position / VaR / hedge orchestration and dashboard.

Follows the immutable calculation-run lifecycle established by
``app.services.regulatory_capital`` and mirrored by ``app.services.regulatory_irr``:
runs commit ``queued`` and ``running`` before executing, persist the full
canonical input snapshot with a SHA-256 ``input_hash``, and record failures as
data (named error codes) rather than HTTP 500s. The arithmetic itself lives in
the pure engine at ``app.domain.fx.engine``.

Every FX run computes the complete foreign-exchange analysis - per-currency and
aggregate net open position vs Tier 1, 99% 1-day historical-simulation VaR (base
and cedi-crisis stressed), hedge effectiveness, and the shocked NOP under each
cedi-depreciation scenario. ``scenario_code`` tags which scenario the run
highlights; the stored metrics and line items are the full analysis so any run
is a self-contained snapshot. Tier 1 capital is read from the capital-component
facts at run time as the FX limit denominator but is deliberately kept OUT of the
input hash, so the FX hash scopes reproducibility to the FX positions, return
histories, hedges, and FX parameters.
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
from app.domain.capital.engine import CapitalFact, tier1_capital
from app.domain.fx.engine import (
    FxComputationError,
    FxHedge,
    FxPosition,
    FxScenarioNop,
    HedgeResult,
    MissingParameterError,
    NopResult,
    VarResult,
    assess_hedges,
    classify_limit,
    compute_nop,
    compute_stressed_var,
    compute_var,
    run_fx_scenarios,
    stressed_var_line_item,
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
from app.schemas.regulatory_fx import (
    FxCurrencyPositionRead,
    FxDashboardRead,
    FxHedgeRead,
    FxMetricsRead,
    FxScenarioBatchCreate,
    FxScenarioNopRead,
    FxStandaloneVarRead,
    FxTrendPointRead,
    FxValidationRead,
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
    worst_status,
)
from app.services.params import get_active_params
from app.services.regulatory_liquidity import get_regulatory_run

ENGINE_VERSION = "regulatory-fx-v1.0.0"
INPUT_SCHEMA_VERSION = "bank-facts-v2"
OUTPUT_SCHEMA_VERSION = "fx-metrics-v1"
MODULE_FX = "fx"
BASELINE_SCENARIO = "baseline"
SCENARIO_MILD = "mild_depreciation"
SCENARIO_SEVERE = "severe_depreciation"
SCENARIO_CRISIS = "cedi_crisis"
FX_RUN_SCENARIO_CODES = (BASELINE_SCENARIO, SCENARIO_MILD, SCENARIO_SEVERE, SCENARIO_CRISIS)

NOP_SINGLE_LIMIT = "fx_nop_single_limit_pct"
NOP_AGGREGATE_LIMIT = "fx_nop_aggregate_limit_pct"
VAR_CONFIDENCE = "fx_var_confidence_pct"
HEDGE_R2_MIN = "hedge_r2_min_pct"
HEDGE_OFFSET_LOW = "hedge_offset_low_pct"
HEDGE_OFFSET_HIGH = "hedge_offset_high_pct"
_REQUIRED_THRESHOLDS = (
    NOP_SINGLE_LIMIT,
    NOP_AGGREGATE_LIMIT,
    VAR_CONFIDENCE,
    HEDGE_R2_MIN,
    HEDGE_OFFSET_LOW,
    HEDGE_OFFSET_HIGH,
)

SHOCK_DEPRECIATION = "ghs_usd_shock_pct"
SHOCK_CORRELATION_UPLIFT = "correlation_uplift"
SHOCK_CRISIS_START = "crisis_window_start"
SHOCK_CRISIS_END = "crisis_window_end"
_DEPRECIATION_SCENARIOS = (SCENARIO_MILD, SCENARIO_SEVERE, SCENARIO_CRISIS)

# Only these fact groups participate in the FX module; keeping the snapshot
# scoped to them makes the input hash insensitive to unrelated fact edits.
_FX_FACT_GROUPS = ("fx_position", "fx_return_history", "fx_hedge")
_CAPITAL_COMPONENT_GROUP = "capital_component"

_ZERO = Decimal("0")


class FxRunError(Exception):
    """Domain input failure persisted onto the run instead of raising HTTP 500."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


@dataclass(frozen=True)
class _FxParams:
    single_limit_pct: Decimal
    aggregate_limit_pct: Decimal
    var_confidence_pct: Decimal
    hedge_r2_min_pct: Decimal
    hedge_offset_low_pct: Decimal
    hedge_offset_high_pct: Decimal
    depreciation_shocks: dict[str, Decimal]
    crisis_window: tuple[int, int]
    correlation_uplift: Decimal


@dataclass(frozen=True)
class _FxAnalysis:
    nop: NopResult
    var: VarResult
    stressed_var: Decimal
    hedges: HedgeResult
    scenarios: tuple[FxScenarioNop, ...]
    correlation_uplift: Decimal


def run_all_fx_scenarios(
    db: Session, ctx: TenantContext, bank_id: UUID, payload: FxScenarioBatchCreate
) -> RegulatoryRunBatchRead:
    _require_actor(ctx)
    bank = _get_bank_or_404(db, ctx, bank_id)
    period = _get_period_or_404(db, ctx, bank, payload.reporting_period_id)
    runs = [
        _create_and_execute(db, ctx, bank, period, scenario_code)
        for scenario_code in FX_RUN_SCENARIO_CODES
    ]
    return RegulatoryRunBatchRead(bank_id=bank.id, reporting_period_id=period.id, runs=runs)


def get_fx_dashboard(
    db: Session, ctx: TenantContext, bank_id: UUID, reporting_period_id: UUID | None = None
) -> FxDashboardRead:
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
        positions = _positions_from_run(latest_run)
        standalone_vars = _standalone_from_run(latest_run)
        hedges = _hedges_read_from_run(latest_run)
        scenarios = _scenarios_from_run(latest_run)
        validations = [
            FxValidationRead(
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
        positions = _positions_from_analysis(analysis)
        standalone_vars = _standalone_from_analysis(analysis)
        hedges = _hedges_read_from_analysis(analysis)
        scenarios = _scenarios_from_analysis(analysis)
        validations = [
            FxValidationRead(
                rule_code=rule_code,
                passed=passed,
                severity=severity,  # type: ignore[arg-type]
                message=message,
            )
            for rule_code, passed, severity, message in _validation_rows(analysis)
        ]
        stored = False

    return FxDashboardRead(
        bank=BankRead.model_validate(bank, from_attributes=True),
        period=BankReportingPeriodRead.model_validate(period, from_attributes=True),
        stored=stored,
        latest_run_id=latest_run.id if latest_run is not None else None,
        metrics=metrics,
        positions=positions,
        standalone_vars=standalone_vars,
        hedges=hedges,
        scenarios=scenarios,
        trend=_build_trend(db, ctx, bank, periods),
        validations=validations,
        live=live_block(db, ctx, bank.id, period.id, MODULE_FX),
    )


def _create_and_execute(
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    period: BankReportingPeriod,
    scenario_code: str,
) -> RegulatoryRunRead:
    facts = _load_facts(db, ctx, bank, period)
    active = _load_fx_params_or_none(db, ctx, bank, period.period_end)
    snapshot = _build_snapshot(bank, period, scenario_code, facts, active)

    run = RegulatoryRun(
        organization_id=ctx.organization_id,
        bank_id=bank.id,
        reporting_period_id=period.id,
        module=MODULE_FX,
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
            "module": MODULE_FX,
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
        analysis = _run_analysis(db, ctx, bank, period, facts, active)
        _persist_success(db, ctx, run, analysis)
    except FxRunError as exc:
        _persist_failure(db, ctx, run_id, exc)
    except MissingParameterError as exc:
        _persist_failure(
            db,
            ctx,
            run_id,
            FxRunError(
                "missing_parameter",
                f"No active FX parameter or return history covers '{exc.name}'.",
                {"parameter": exc.name},
            ),
        )
    except FxComputationError as exc:
        _persist_failure(db, ctx, run_id, FxRunError("calculation_error", str(exc), None))
    except HTTPException:
        raise
    except Exception:
        _persist_failure(
            db,
            ctx,
            run_id,
            FxRunError(
                "calculation_error",
                "The FX metrics could not be calculated.",
                {
                    "corrective_action": (
                        "Review the run inputs and retry. Contact support if it fails again."
                    )
                },
            ),
        )
    db.expire_all()
    return get_regulatory_run(db, ctx, bank.id, run_id)


def _run_analysis(  # noqa: PLR0913
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    period: BankReportingPeriod,
    facts: list[BankFinancialFact],
    active: _FxParams | None,
) -> _FxAnalysis:
    if not facts:
        raise FxRunError(
            "financial_facts_missing",
            "The reporting period has no foreign-exchange positions to analyze.",
            {"reporting_period_id": str(period.id)},
        )
    if active is None:
        raise FxRunError(
            "missing_parameter",
            "Required FX parameters (NOP limits, VaR confidence, hedge bands, or "
            "depreciation scenarios) are not configured.",
            None,
        )
    tier1 = _load_tier1(db, ctx, bank, period)
    if tier1 <= _ZERO:
        raise FxRunError(
            "missing_parameter",
            "Tier 1 capital could not be derived from the capital-component facts.",
            None,
        )
    positions = _positions_from_facts(facts)
    if not positions:
        raise FxRunError(
            "financial_facts_missing",
            "The reporting period has no foreign-exchange positions to analyze.",
            {"reporting_period_id": str(period.id)},
        )
    return_histories = _returns_from_facts(facts)
    hedges = _hedges_from_facts(facts)

    nop = compute_nop(positions, tier1, active.single_limit_pct, active.aggregate_limit_pct)
    var = compute_var(positions, return_histories, active.var_confidence_pct)
    stressed = compute_stressed_var(
        positions,
        return_histories,
        active.var_confidence_pct,
        active.crisis_window,
        active.correlation_uplift,
    )
    scenario_shocks: dict[str, Decimal] = {BASELINE_SCENARIO: _ZERO}
    for code in _DEPRECIATION_SCENARIOS:
        scenario_shocks[code] = active.depreciation_shocks[code]
    scenarios = run_fx_scenarios(
        positions, tier1, scenario_shocks, active.single_limit_pct, active.aggregate_limit_pct
    )
    hedge_result = assess_hedges(
        hedges, active.hedge_r2_min_pct, active.hedge_offset_low_pct, active.hedge_offset_high_pct
    )
    return _FxAnalysis(
        nop=nop,
        var=var,
        stressed_var=stressed,
        hedges=hedge_result,
        scenarios=scenarios,
        correlation_uplift=active.correlation_uplift,
    )


def _persist_success(
    db: Session, ctx: TenantContext, run: RegulatoryRun, analysis: _FxAnalysis
) -> None:
    run.metrics = _metrics_payload(analysis)

    nop = analysis.nop
    nop_status = classify_limit(nop.nop_pct_tier1, nop.aggregate_limit_pct)
    single_status = classify_limit(nop.single_ccy_max_pct, nop.single_limit_pct)
    metric_rows: list[tuple[str, Decimal, str, Decimal | None, str]] = [
        ("nop_pct_tier1", nop.nop_pct_tier1, "pct", nop.aggregate_limit_pct, nop_status),
        ("single_ccy_max_pct", nop.single_ccy_max_pct, "pct", nop.single_limit_pct, single_status),
        ("nop_ghs", nop.overall_nop, "ghs", None, "na"),
        ("var_99_1d_ghs", analysis.var.portfolio_var, "ghs", None, "na"),
        ("stressed_var_ghs", analysis.stressed_var, "ghs", None, "na"),
        (
            "diversification_benefit_ghs",
            analysis.var.diversification_benefit,
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
        *analysis.nop.line_items,
        *analysis.var.line_items,
        stressed_var_line_item(analysis.stressed_var, analysis.correlation_uplift),
        *analysis.hedges.line_items,
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
            "nop_pct_tier1": str(nop.nop_pct_tier1),
            "var_99_1d_ghs": str(analysis.var.portfolio_var),
        },
    )
    db.commit()


def _persist_failure(db: Session, ctx: TenantContext, run_id: UUID, error: FxRunError) -> None:
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


def _metrics_payload(analysis: _FxAnalysis) -> dict[str, Any]:
    nop = analysis.nop
    var = analysis.var
    return {
        "nop_ghs": str(nop.overall_nop),
        "nop_pct_tier1": str(nop.nop_pct_tier1),
        "sum_long_ghs": str(nop.sum_long),
        "sum_short_ghs": str(nop.sum_short),
        "single_ccy_max_pct": str(nop.single_ccy_max_pct),
        "single_ccy_max_currency": nop.single_ccy_max_currency,
        "nop_single_limit_pct": str(nop.single_limit_pct),
        "nop_aggregate_limit_pct": str(nop.aggregate_limit_pct),
        "within_single_limit": nop.within_single_limit,
        "within_aggregate_limit": nop.within_aggregate_limit,
        "var_99_1d_ghs": str(var.portfolio_var),
        "stressed_var_ghs": str(analysis.stressed_var),
        "diversification_benefit_ghs": str(var.diversification_benefit),
        "standalone_var_total_ghs": str(var.standalone_total),
        "var_confidence_pct": str(var.confidence_pct),
        "var_observations": var.observations,
        "correlation_uplift": str(analysis.correlation_uplift),
        "hedge_effective_count": analysis.hedges.effective_count,
        "hedge_ineffective_count": analysis.hedges.ineffective_count,
        "hedge_total_count": analysis.hedges.total_count,
        "hedge_aggregate_mtm_ghs": str(analysis.hedges.aggregate_mtm_ghs),
        "tier1_ghs": str(nop.tier1),
        "currencies": [
            {
                "currency": currency.currency,
                "side": currency.side,
                "net_ghs": str(currency.net_ghs),
                "net_ccy": str(currency.net_ccy),
                "spot_ghs": str(currency.spot_ghs),
                "abs_pct_tier1": str(currency.abs_pct_tier1),
                "within_single_limit": currency.within_single_limit,
            }
            for currency in nop.currencies
        ],
        "standalone_vars": [
            {
                "currency": currency_var.currency,
                "net_ghs": str(currency_var.net_ghs),
                "standalone_var_ghs": str(currency_var.standalone_var),
            }
            for currency_var in var.currency_vars
        ],
        "hedges": [
            {
                "hedge_id": hedge.hedge_id,
                "instrument": hedge.instrument,
                "pair": hedge.pair,
                "mtm_ghs": str(hedge.mtm_ghs),
                "prospective_r2_pct": str(hedge.prospective_r2_pct),
                "dollar_offset_pct": str(hedge.dollar_offset_pct),
                "effective": hedge.effective,
            }
            for hedge in analysis.hedges.hedges
        ],
        "nop_by_scenario": [
            {
                "scenario_code": scenario.scenario_code,
                "shock_pct": str(scenario.shock_pct),
                "nop_ghs": str(scenario.nop_ghs),
                "nop_pct_tier1": str(scenario.nop_pct_tier1),
                "within_aggregate_limit": scenario.within_aggregate_limit,
            }
            for scenario in analysis.scenarios
        ],
    }


def _validation_rows(analysis: _FxAnalysis) -> tuple[tuple[str, bool, str, str], ...]:
    nop = analysis.nop
    aggregate_limit = _pct_text(nop.aggregate_limit_pct)
    single_limit = _pct_text(nop.single_limit_pct)
    nop_message = (
        f"The aggregate net open position of {_pct_text(nop.nop_pct_tier1)}% of Tier 1 is "
        + ("within" if nop.within_aggregate_limit else "above")
        + f" the {aggregate_limit}% BoG aggregate limit."
    )
    single_message = (
        f"The largest single-currency net open position ({nop.single_ccy_max_currency} at "
        f"{_pct_text(nop.single_ccy_max_pct)}% of Tier 1) is "
        + ("within" if nop.within_single_limit else "above")
        + f" the {single_limit}% single-currency limit."
    )
    hedges_effective = analysis.hedges.ineffective_count == 0
    hedge_message = (
        f"{analysis.hedges.effective_count} of {analysis.hedges.total_count} hedges pass the "
        "IFRS 9 dual effectiveness test (R-squared >= 80% and dollar-offset within 80-125%)."
    )
    stressed_message = (
        f"The cedi-crisis stressed VaR of {_ghs_text(analysis.stressed_var)} GHS "
        f"(vs a base VaR of {_ghs_text(analysis.var.portfolio_var)} GHS) is disclosed."
    )
    return (
        ("nop_within_aggregate_limit", nop.within_aggregate_limit, "error", nop_message),
        ("single_ccy_within_limit", nop.within_single_limit, "error", single_message),
        ("hedges_effective", hedges_effective, "warning", hedge_message),
        ("stressed_var_disclosed", True, "info", stressed_message),
    )


def _metrics_from_analysis(analysis: _FxAnalysis) -> FxMetricsRead:
    nop = analysis.nop
    var = analysis.var
    return FxMetricsRead(
        nop_ghs=nop.overall_nop,
        nop_pct_tier1=nop.nop_pct_tier1,
        nop_status=classify_limit(nop.nop_pct_tier1, nop.aggregate_limit_pct),
        sum_long_ghs=nop.sum_long,
        sum_short_ghs=nop.sum_short,
        single_ccy_max_pct=nop.single_ccy_max_pct,
        single_ccy_max_currency=nop.single_ccy_max_currency,
        single_ccy_status=classify_limit(nop.single_ccy_max_pct, nop.single_limit_pct),
        nop_single_limit_pct=nop.single_limit_pct,
        nop_aggregate_limit_pct=nop.aggregate_limit_pct,
        var_99_1d_ghs=var.portfolio_var,
        stressed_var_ghs=analysis.stressed_var,
        diversification_benefit_ghs=var.diversification_benefit,
        standalone_var_total_ghs=var.standalone_total,
        var_confidence_pct=var.confidence_pct,
        var_observations=var.observations,
        hedge_effective_count=analysis.hedges.effective_count,
        hedge_total_count=analysis.hedges.total_count,
        hedge_aggregate_mtm_ghs=analysis.hedges.aggregate_mtm_ghs,
        tier1_ghs=nop.tier1,
    )


def _positions_from_analysis(analysis: _FxAnalysis) -> list[FxCurrencyPositionRead]:
    return [
        FxCurrencyPositionRead(
            currency=currency.currency,
            side=currency.side,
            net_ghs=currency.net_ghs,
            net_ccy=currency.net_ccy,
            spot_ghs=currency.spot_ghs,
            abs_pct_tier1=currency.abs_pct_tier1,
            within_single_limit=currency.within_single_limit,
        )
        for currency in analysis.nop.currencies
    ]


def _standalone_from_analysis(analysis: _FxAnalysis) -> list[FxStandaloneVarRead]:
    return [
        FxStandaloneVarRead(
            currency=currency_var.currency,
            net_ghs=currency_var.net_ghs,
            standalone_var_ghs=currency_var.standalone_var,
        )
        for currency_var in analysis.var.currency_vars
    ]


def _hedges_read_from_analysis(analysis: _FxAnalysis) -> list[FxHedgeRead]:
    return [
        FxHedgeRead(
            hedge_id=hedge.hedge_id,
            instrument=hedge.instrument,
            pair=hedge.pair,
            mtm_ghs=hedge.mtm_ghs,
            prospective_r2_pct=hedge.prospective_r2_pct,
            dollar_offset_pct=hedge.dollar_offset_pct,
            effective=hedge.effective,
        )
        for hedge in analysis.hedges.hedges
    ]


def _scenarios_from_analysis(analysis: _FxAnalysis) -> list[FxScenarioNopRead]:
    return [
        FxScenarioNopRead(
            scenario_code=scenario.scenario_code,  # type: ignore[arg-type]
            shock_pct=scenario.shock_pct,
            nop_ghs=scenario.nop_ghs,
            nop_pct_tier1=scenario.nop_pct_tier1,
            within_aggregate_limit=scenario.within_aggregate_limit,
        )
        for scenario in analysis.scenarios
    ]


def _metrics_from_run(run: RegulatoryRun) -> FxMetricsRead:
    metrics = run.metrics
    nop_pct = _decimal(metrics, "nop_pct_tier1")
    aggregate_limit = _decimal(metrics, "nop_aggregate_limit_pct")
    single_max = _decimal(metrics, "single_ccy_max_pct")
    single_limit = _decimal(metrics, "nop_single_limit_pct")
    return FxMetricsRead(
        nop_ghs=_decimal(metrics, "nop_ghs"),
        nop_pct_tier1=nop_pct,
        nop_status=classify_limit(nop_pct, aggregate_limit),
        sum_long_ghs=_decimal(metrics, "sum_long_ghs"),
        sum_short_ghs=_decimal(metrics, "sum_short_ghs"),
        single_ccy_max_pct=single_max,
        single_ccy_max_currency=metrics["single_ccy_max_currency"],
        single_ccy_status=classify_limit(single_max, single_limit),
        nop_single_limit_pct=single_limit,
        nop_aggregate_limit_pct=aggregate_limit,
        var_99_1d_ghs=_decimal(metrics, "var_99_1d_ghs"),
        stressed_var_ghs=_decimal(metrics, "stressed_var_ghs"),
        diversification_benefit_ghs=_decimal(metrics, "diversification_benefit_ghs"),
        standalone_var_total_ghs=_decimal(metrics, "standalone_var_total_ghs"),
        var_confidence_pct=_decimal(metrics, "var_confidence_pct"),
        var_observations=int(metrics["var_observations"]),
        hedge_effective_count=int(metrics["hedge_effective_count"]),
        hedge_total_count=int(metrics["hedge_total_count"]),
        hedge_aggregate_mtm_ghs=_decimal(metrics, "hedge_aggregate_mtm_ghs"),
        tier1_ghs=_decimal(metrics, "tier1_ghs"),
    )


def _positions_from_run(run: RegulatoryRun) -> list[FxCurrencyPositionRead]:
    currencies: list[dict[str, Any]] = run.metrics.get("currencies", [])
    return [
        FxCurrencyPositionRead(
            currency=currency["currency"],
            side=currency["side"],
            net_ghs=Decimal(str(currency["net_ghs"])),
            net_ccy=Decimal(str(currency["net_ccy"])),
            spot_ghs=Decimal(str(currency["spot_ghs"])),
            abs_pct_tier1=Decimal(str(currency["abs_pct_tier1"])),
            within_single_limit=bool(currency["within_single_limit"]),
        )
        for currency in currencies
    ]


def _standalone_from_run(run: RegulatoryRun) -> list[FxStandaloneVarRead]:
    standalone_vars: list[dict[str, Any]] = run.metrics.get("standalone_vars", [])
    return [
        FxStandaloneVarRead(
            currency=currency_var["currency"],
            net_ghs=Decimal(str(currency_var["net_ghs"])),
            standalone_var_ghs=Decimal(str(currency_var["standalone_var_ghs"])),
        )
        for currency_var in standalone_vars
    ]


def _hedges_read_from_run(run: RegulatoryRun) -> list[FxHedgeRead]:
    hedges: list[dict[str, Any]] = run.metrics.get("hedges", [])
    return [
        FxHedgeRead(
            hedge_id=hedge["hedge_id"],
            instrument=hedge["instrument"],
            pair=hedge["pair"],
            mtm_ghs=Decimal(str(hedge["mtm_ghs"])),
            prospective_r2_pct=Decimal(str(hedge["prospective_r2_pct"])),
            dollar_offset_pct=Decimal(str(hedge["dollar_offset_pct"])),
            effective=bool(hedge["effective"]),
        )
        for hedge in hedges
    ]


def _scenarios_from_run(run: RegulatoryRun) -> list[FxScenarioNopRead]:
    scenarios: list[dict[str, Any]] = run.metrics.get("nop_by_scenario", [])
    return [
        FxScenarioNopRead(
            scenario_code=scenario["scenario_code"],
            shock_pct=Decimal(str(scenario["shock_pct"])),
            nop_ghs=Decimal(str(scenario["nop_ghs"])),
            nop_pct_tier1=Decimal(str(scenario["nop_pct_tier1"])),
            within_aggregate_limit=bool(scenario["within_aggregate_limit"]),
        )
        for scenario in scenarios
    ]


def _build_trend(
    db: Session, ctx: TenantContext, bank: Bank, periods: list[BankReportingPeriod]
) -> list[FxTrendPointRead]:
    points: list[FxTrendPointRead] = []
    for period in periods:
        run = _latest_succeeded_baseline_run(db, ctx, bank, period.id)
        if run is not None:
            metrics = run.metrics
            points.append(
                FxTrendPointRead(
                    reporting_period_id=period.id,
                    label=period.label,
                    period_end=period.period_end,
                    nop_ghs=_decimal(metrics, "nop_ghs"),
                    nop_pct_tier1=_decimal(metrics, "nop_pct_tier1"),
                    var_99_1d_ghs=_decimal(metrics, "var_99_1d_ghs"),
                    stored=True,
                )
            )
            continue
        try:
            analysis = _compute_inline(db, ctx, bank, period)
        except (MissingParameterError, FxComputationError, FxRunError):
            continue
        points.append(
            FxTrendPointRead(
                reporting_period_id=period.id,
                label=period.label,
                period_end=period.period_end,
                nop_ghs=analysis.nop.overall_nop,
                nop_pct_tier1=analysis.nop.nop_pct_tier1,
                var_99_1d_ghs=analysis.var.portfolio_var,
                stored=False,
            )
        )
    return points


def _compute_inline(
    db: Session, ctx: TenantContext, bank: Bank, period: BankReportingPeriod
) -> _FxAnalysis:
    facts = _load_facts(db, ctx, bank, period)
    active = _load_fx_params_or_none(db, ctx, bank, period.period_end)
    return _run_analysis(db, ctx, bank, period, facts, active)


def _compute_inline_or_409(
    db: Session, ctx: TenantContext, bank: Bank, period: BankReportingPeriod
) -> _FxAnalysis:
    try:
        return _compute_inline(db, ctx, bank, period)
    except MissingParameterError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error_code": "missing_parameter", "message": str(exc), "parameter": exc.name},
        ) from exc
    except FxRunError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error_code": exc.code, "message": exc.message},
        ) from exc
    except FxComputationError as exc:
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
    active = _load_fx_params_or_none(db, ctx, bank, period.period_end)
    snapshot = _build_snapshot(bank, period, BASELINE_SCENARIO, facts, active)
    return _snapshot_hash(snapshot)


def compute_live(
    db: Session, ctx: TenantContext, bank: Bank, period: BankReportingPeriod
) -> LiveModuleResult:
    """Cheap baseline live view — reuses the dashboard's unstored-branch path
    and creates no RegulatoryRun. An unhedged NOP breach surfaces here as a
    failed error-severity validation, i.e. an alert-worthy finding."""
    analysis = _compute_inline(db, ctx, bank, period)
    metrics = _metrics_from_analysis(analysis)
    live_metrics = {
        "nop_ghs": str(metrics.nop_ghs),
        "nop_pct_tier1": str(metrics.nop_pct_tier1),
        "single_ccy_max_pct": str(metrics.single_ccy_max_pct),
        "single_ccy_max_currency": metrics.single_ccy_max_currency,
        "var_99_1d_ghs": str(metrics.var_99_1d_ghs),
        "tier1_ghs": str(metrics.tier1_ghs),
    }
    status = worst_status(metrics.nop_status, metrics.single_ccy_status)
    findings = findings_from_validations(_validation_rows(analysis), status)
    return LiveModuleResult(
        metrics=live_metrics,
        status=status,
        input_hash=current_input_hash(db, ctx, bank, period),
        findings=findings,
    )


def _positions_from_facts(facts: list[BankFinancialFact]) -> list[FxPosition]:
    positions: list[FxPosition] = []
    for fact in facts:
        if fact.fact_group != "fx_position":
            continue
        attributes = fact.attributes
        positions.append(
            FxPosition(
                currency=attributes["currency"],
                net_ghs=Decimal(str(fact.amount)),
                spot_ghs=Decimal(str(attributes["spot_ghs"])),
                net_ccy=Decimal(str(attributes["net_ccy"])),
                assets_ccy=Decimal(str(attributes["assets_ccy"])),
                liabilities_ccy=Decimal(str(attributes["liabilities_ccy"])),
                net_derivatives_ccy=Decimal(str(attributes["net_derivatives_ccy"])),
            )
        )
    return positions


def _returns_from_facts(facts: list[BankFinancialFact]) -> dict[str, list[float]]:
    histories: dict[str, list[float]] = {}
    for fact in facts:
        if fact.fact_group != "fx_return_history":
            continue
        attributes = fact.attributes
        histories[attributes["currency"]] = [float(value) for value in attributes["returns"]]
    return histories


def _hedges_from_facts(facts: list[BankFinancialFact]) -> list[FxHedge]:
    hedges: list[FxHedge] = []
    for fact in facts:
        if fact.fact_group != "fx_hedge":
            continue
        attributes = fact.attributes
        hedges.append(
            FxHedge(
                hedge_id=attributes["hedge_id"],
                instrument=attributes["instrument"],
                pair=attributes["pair"],
                mtm_ghs=Decimal(str(fact.amount)),
                prospective_r2=Decimal(str(attributes["prospective_r2"])),
                dollar_offset_ratio=Decimal(str(attributes["dollar_offset_ratio"])),
            )
        )
    return hedges


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
                BankFinancialFact.fact_group.in_(_FX_FACT_GROUPS),
            )
            .order_by(BankFinancialFact.fact_group, BankFinancialFact.category)
        )
    )


def _load_tier1(
    db: Session, ctx: TenantContext, bank: Bank, period: BankReportingPeriod
) -> Decimal:
    components = list(
        db.scalars(
            select(BankFinancialFact).where(
                BankFinancialFact.organization_id == ctx.organization_id,
                BankFinancialFact.bank_id == bank.id,
                BankFinancialFact.reporting_period_id == period.id,
                BankFinancialFact.fact_group == _CAPITAL_COMPONENT_GROUP,
            )
        )
    )
    capital_facts = [
        CapitalFact(
            fact_group=_CAPITAL_COMPONENT_GROUP,
            category=fact.category,
            amount=Decimal(str(fact.amount)),
            capital_tier=fact.capital_tier,
            is_deduction=fact.is_deduction,
        )
        for fact in components
    ]
    return tier1_capital(capital_facts)


def _load_fx_params_or_none(
    db: Session, ctx: TenantContext, bank: Bank, as_of: date
) -> _FxParams | None:
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
        if row.module != MODULE_FX:
            continue
        scenario_shocks.setdefault(row.scenario_code, {})[row.shock_key] = Decimal(
            str(row.shock_value)
        )

    depreciation_shocks: dict[str, Decimal] = {}
    for code in _DEPRECIATION_SCENARIOS:
        scenario = scenario_shocks.get(code)
        if scenario is None or SHOCK_DEPRECIATION not in scenario:
            return None
        depreciation_shocks[code] = scenario[SHOCK_DEPRECIATION]

    crisis = scenario_shocks.get(SCENARIO_CRISIS, {})
    if any(
        key not in crisis
        for key in (SHOCK_CORRELATION_UPLIFT, SHOCK_CRISIS_START, SHOCK_CRISIS_END)
    ):
        return None

    return _FxParams(
        single_limit_pct=thresholds[NOP_SINGLE_LIMIT],
        aggregate_limit_pct=thresholds[NOP_AGGREGATE_LIMIT],
        var_confidence_pct=thresholds[VAR_CONFIDENCE],
        hedge_r2_min_pct=thresholds[HEDGE_R2_MIN],
        hedge_offset_low_pct=thresholds[HEDGE_OFFSET_LOW],
        hedge_offset_high_pct=thresholds[HEDGE_OFFSET_HIGH],
        depreciation_shocks=depreciation_shocks,
        crisis_window=(int(crisis[SHOCK_CRISIS_START]), int(crisis[SHOCK_CRISIS_END])),
        correlation_uplift=crisis[SHOCK_CORRELATION_UPLIFT],
    )


def _build_snapshot(
    bank: Bank,
    period: BankReportingPeriod,
    scenario_code: str,
    facts: list[BankFinancialFact],
    active: _FxParams | None,
) -> dict[str, Any]:
    return {
        "schema_version": INPUT_SCHEMA_VERSION,
        "module": MODULE_FX,
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


def _snapshot_parameters(active: _FxParams | None) -> dict[str, Any]:
    if active is None:
        return {
            "limits_pct": {},
            "hedge_bands_pct": {},
            "depreciation_shocks_pct": {},
            "crisis": {},
        }
    return {
        "limits_pct": {
            NOP_SINGLE_LIMIT: str(active.single_limit_pct),
            NOP_AGGREGATE_LIMIT: str(active.aggregate_limit_pct),
            VAR_CONFIDENCE: str(active.var_confidence_pct),
        },
        "hedge_bands_pct": {
            HEDGE_R2_MIN: str(active.hedge_r2_min_pct),
            HEDGE_OFFSET_LOW: str(active.hedge_offset_low_pct),
            HEDGE_OFFSET_HIGH: str(active.hedge_offset_high_pct),
        },
        "depreciation_shocks_pct": {
            code: str(shock) for code, shock in sorted(active.depreciation_shocks.items())
        },
        "crisis": {
            "window_start": active.crisis_window[0],
            "window_end": active.crisis_window[1],
            "correlation_uplift": str(active.correlation_uplift),
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


def _ghs_text(value: Decimal) -> str:
    return format(value, "f")


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
            RegulatoryRun.module == MODULE_FX,
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
