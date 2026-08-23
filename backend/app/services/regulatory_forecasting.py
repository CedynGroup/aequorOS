"""Balance-sheet forecasting runs: 5-year projection, strategic optimizer, what-if.

Follows the immutable calculation-run lifecycle established by
``app.services.regulatory_liquidity``: runs commit ``queued`` and ``running``
before executing, persist the full canonical input snapshot with a SHA-256
``input_hash``, and record failures as data (named error codes) rather than
HTTP 500s. The arithmetic itself lives in the pure engine at
``app.domain.forecasting.engine``, which computes the projected regulatory
ratios by calling the liquidity and capital engines on the projected fact
sets. Projection outputs are persisted in the run's ``metrics`` JSON
(stringified Decimals); forecast runs additionally persist headline
``RegulatoryMetricResult`` rows and ``RegulatoryValidation`` rows, while
optimizer and what-if runs carry their full result payload in ``metrics``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, replace
from dataclasses import field as dataclass_field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.domain.capital.ecl import EclAssumption
from app.domain.capital.engine import (
    CapitalComputationError,
    CapitalParams,
    classify_capital_ratio,
)
from app.domain.capital.engine import (
    MissingParameterError as CapitalMissingParameterError,
)
from app.domain.forecasting.engine import (
    DEFAULT_FEE_INCOME_PCT_ASSETS,
    DEFAULT_SECURITIES_SHIFT_PP,
    DEFAULT_TAX_RATE_PCT,
    PROJECTION_YEARS,
    WHATIF_SHOCKS,
    ForecastAssumptions,
    ForecastFact,
    ForecastParams,
    OptimizerCandidateResult,
    OptimizerConstraints,
    OptimizerDecision,
    OptimizerResult,
    ProjectionError,
    ProjectionResult,
    ProjectionSummary,
    ProjectionYear,
    UnknownShockError,
    WhatIfMetricComparison,
    WhatIfResult,
    project,
    run_optimizer,
    run_whatif,
)
from app.domain.liquidity.engine import (
    FACT_GROUP_SECURITIES,
    HQLA_LEVEL_1,
    HQLA_LEVELS,
    LiquidityComputationError,
    LiquidityParams,
    classify_ratio,
)
from app.domain.liquidity.engine import (
    MissingParameterError as LiquidityMissingParameterError,
)
from app.models import (
    Bank,
    BankFinancialFact,
    BankReportingPeriod,
    FinancialFactRow,
    ParamCapitalThreshold,
    ParamCrmHaircut,
    ParamEclAssumption,
    ParamLcrRunoffRate,
    ParamNsfrWeight,
    ParamRiskWeight,
    ParamStressShock,
    RegulatoryMetricResult,
    RegulatoryRun,
    RegulatoryValidation,
)
from app.schemas.forecasting import (
    ForecastAssumptionDefaultsRead,
    ForecastAssumptionsRead,
    ForecastAssumptionsUpdate,
    ForecastRunCreate,
    ForecastRunListRead,
    ForecastRunRead,
    ForecastRunSummaryRead,
    ForecastScenarioListRead,
    ForecastScenarioRead,
    OptimizerCandidateRead,
    OptimizerConstraintStatusRead,
    OptimizerDecisionRead,
    OptimizerResultRead,
    OptimizerRunCreate,
    ProjectionSummaryRead,
    ProjectionYearRead,
    WhatIfMetricComparisonRead,
    WhatIfResultRead,
    WhatIfRunCreate,
    WhatIfYear5ComparisonRead,
    WhatIfYearDeltaRead,
)
from app.schemas.regulatory_liquidity import (
    RegulatoryMetricResultRead,
    RegulatoryRunErrorRead,
    RegulatoryValidationRead,
)
from app.services import regulatory_parameters, sdi_regime
from app.services.audit import record_event
from app.services.live_state import current_snapshot, load_current_facts
from app.services.live_types import LiveModuleResult, findings_from_validations, worst_status
from app.services.params import get_active_params
from app.services.regulatory_capital import DEFAULT_CRM_HAIRCUTS

ENGINE_VERSION = "regulatory-forecasting-v1.0.0"
INPUT_SCHEMA_VERSION = "bank-facts-v2"
OUTPUT_SCHEMA_VERSION = "forecast-projection-v1"
MODULE_FORECAST = "forecast"
MODULE_OPTIMIZER = "optimizer"
MODULE_WHATIF = "whatif"
BASE_SCENARIO = "base"
CUSTOM_SCENARIO = "custom"
OPTIMIZER_SCENARIO = "constrained_search"
FORECAST_PRESET_CODES = ("base", "adverse", "severely_adverse")

_ZERO = Decimal("0")
ASSUMPTION_KEYS = (
    "loan_growth_pct",
    "deposit_growth_pct",
    "nim_pct",
    "cost_to_income_pct",
    "credit_loss_rate_pct",
    "fx_depreciation_pct",
    "dividend_payout_pct",
)
_EXTRA_ASSUMPTION_KEYS = ("fee_income_pct_assets", "tax_rate_pct", "securities_shift_pp")
_ALL_ASSUMPTION_KEYS = (*ASSUMPTION_KEYS, *_EXTRA_ASSUMPTION_KEYS)
_REQUIRED_LIQUIDITY_THRESHOLDS = ("lcr_min", "lcr_amber_floor", "nsfr_min", "lcr_inflow_cap_pct")
_REQUIRED_CAPITAL_THRESHOLDS = (
    "bia_alpha_pct",
    "car_critical",
    "car_early_warning",
    "car_min",
    "cet1_min",
    "fx_charge_pct",
    "leverage_min",
    "rwa_multiplier",
    "tier1_min",
    "tier2_gp_cap_pct_credit_rwa",
)
# All fact groups both downstream engines consume; the snapshot is scoped to
# them so the input hash ignores unrelated (deposit_behavior) fact edits.
_FORECAST_FACT_GROUPS = (
    "balance_sheet",
    "capital_component",
    # ``crm_collateral`` and ``ecl_exposure`` carry no balance-sheet amount of
    # their own, but the CAPITAL engine reads both — collateral nets down credit
    # RWA and the staged EADs drive the IFRS 9 general-provisions Tier 2
    # override. Omitting them handed the same engine a narrower input set than
    # ``regulatory_capital`` does, so year-0 CAR could differ from the capital
    # run's CAR for the same bank and period (forensic audit 2026-08-21, the
    # High divergence). They are inputs; they belong in the hashed snapshot.
    "crm_collateral",
    "ecl_exposure",
    "lcr_inflow",
    "loan_exposure",
    "market_risk",
    "off_balance",
    "operational_income",
    "securities",
)

_PATH_MONEY_FIELDS = (
    "total_assets",
    "loans",
    "securities",
    "cash",
    "deposits",
    "borrowings_plug",
    "equity",
    "nii",
    "fees",
    "total_income",
    "opex",
    "credit_losses",
    "net_income",
    "dividends",
)
_PATH_RATIO_FIELDS = ("car_pct", "tier1_ratio_pct", "cet1_ratio_pct", "lcr_pct", "nsfr_pct")
_SUMMARY_FIELDS = (
    "avg_roe_pct",
    "year5_car_pct",
    "year5_lcr_pct",
    "year5_nsfr_pct",
    "cumulative_net_income",
    "min_car_pct",
    "min_lcr_pct",
    "min_nsfr_pct",
)


class ForecastRunError(Exception):
    """Domain input failure persisted onto the run instead of raising HTTP 500."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


@dataclass(frozen=True)
class _ActiveForecastParams:
    outflow_rates: dict[str, Decimal]
    inflow_rates: dict[str, Decimal]
    asf_weights: dict[str, Decimal]
    rsf_weights: dict[str, Decimal]
    risk_weights: dict[str, Decimal]
    thresholds: dict[str, Decimal]
    # Phase 2 items 8/9, resolved exactly as ``regulatory_capital`` resolves
    # them so the projection's capital arithmetic is the capital run's.
    crm_haircuts: dict[str, Decimal] = dataclass_field(default_factory=dict)
    ecl_assumptions: tuple[EclAssumption, ...] = ()
    #: Basel HQLA haircuts + Level-2 caps (enterprise audit P0-8). Defaulted to
    #: the empty set so the dataclass stays constructible in the same shape; the
    #: loader always supplies the resolved values, and the pure engine fails
    #: closed on anything it is actually asked to weight without a rate.
    hqla: regulatory_parameters.HqlaParameters = dataclass_field(
        default_factory=lambda: regulatory_parameters.HqlaParameters({}, None, None)
    )


def list_forecast_scenarios(
    db: Session, ctx: TenantContext, bank_id: str
) -> ForecastScenarioListRead:
    bank = _get_bank_or_404(db, ctx, bank_id)
    # The scenario list is the entry screen for the projection; refusing it here
    # is what stops an SDI operator from filling in assumptions for a projection
    # that will not be produced.
    sdi_regime.require_bank_forecast_regime(db, bank)
    as_of = _latest_period_end(db, ctx, bank) or date.today()
    presets = _load_presets(db, ctx, bank, as_of)
    scenarios = [
        ForecastScenarioRead(code=code, assumptions=presets[code])  # type: ignore[arg-type]
        for code in FORECAST_PRESET_CODES
        if code in presets
    ]
    defaults = ForecastAssumptionDefaultsRead(
        fee_income_pct_assets=DEFAULT_FEE_INCOME_PCT_ASSETS,
        tax_rate_pct=DEFAULT_TAX_RATE_PCT,
        securities_shift_pp=DEFAULT_SECURITIES_SHIFT_PP,
    )
    return ForecastScenarioListRead(bank_id=bank.id, scenarios=scenarios, defaults=defaults)


def create_forecast_run(
    db: Session, ctx: TenantContext, bank_id: str, payload: ForecastRunCreate
) -> ForecastRunRead:
    _require_actor(ctx)
    bank = _get_bank_or_404(db, ctx, bank_id)
    # Regime gate BEFORE anything is computed or persisted (forensic architecture
    # audit sections 6 + 10): this projection's compliance outputs are Basel
    # ratios, which are not the regime an SDI is supervised under. No run row is
    # created, so no Basel number for an SDI ever exists to be read back.
    sdi_regime.require_bank_forecast_regime(db, bank)
    period = _get_period_or_404(db, ctx, bank, payload.reporting_period_id)
    facts = _load_facts(db, ctx, bank, period)
    active = _load_active_params(db, ctx, bank, period.period_end)
    presets = _load_presets(db, ctx, bank, period.period_end)

    assumptions, resolution_error = _resolve_or_defer(
        presets, payload.scenario_code, payload.assumptions
    )
    snapshot = _build_snapshot(
        bank,
        period,
        module=MODULE_FORECAST,
        scenario_code=payload.scenario_code,
        facts=facts,
        active=active,
        assumptions=assumptions,
        overrides=payload.assumptions,
        horizon_years=payload.horizon_years,
    )
    run = _create_run_row(db, ctx, bank, period, MODULE_FORECAST, payload.scenario_code, snapshot)

    run_id = run.id
    if assumptions is None:
        _persist_failure(db, ctx, run_id, resolution_error or _missing_assumptions_error())
    else:
        try:
            engine_facts = _engine_facts_or_error(facts, period)
            params = _forecast_engine_params(active)
            projection = project(
                engine_facts,
                params,
                assumptions,
                payload.horizon_years,
                period_labels=_period_labels(period, payload.horizon_years),
            )
            _persist_forecast_success(db, ctx, run, projection, params)
        except HTTPException:
            raise
        except Exception as exc:
            _persist_failure(db, ctx, run_id, _run_error(exc))
    db.expire_all()
    return _read_forecast_run(db, _run_or_404(db, ctx, bank.id, run_id, module=MODULE_FORECAST))


def compute_live(
    db: Session, ctx: TenantContext, bank: Bank, period: BankReportingPeriod
) -> LiveModuleResult:
    """Compute the current five-year baseline without creating a RegulatoryRun.

    Saved scenarios and official forecasts remain immutable runs. This function
    is intentionally the live plane's only forecast entry point.

    Defence in depth: ``pipeline._scoped_modules`` already leaves this module out
    for a class with no registered forecast authority, so the gate below never
    fires from the live tier — but no caller may reach a Basel projection for an
    SDI through any other route either.
    """
    sdi_regime.require_bank_forecast_regime(db, bank)
    current = load_current_facts(db, ctx, bank, _FORECAST_FACT_GROUPS)
    facts = current.facts
    active = _load_active_params(db, ctx, bank, current.source_as_of_date)
    presets = _load_presets(db, ctx, bank, current.source_as_of_date)
    assumptions, resolution_error = _resolve_or_defer(presets, BASE_SCENARIO, None)
    if assumptions is None:
        raise resolution_error or _missing_assumptions_error()
    params = _forecast_engine_params(active)
    projection = project(
        _engine_facts_or_error(facts, period),
        params,
        assumptions,
        5,
        period_labels=_live_period_labels(current.source_as_of_date, 5),
    )
    summary = projection.summary
    validations = _validation_rows(summary, params, projection.years)
    statuses = (
        classify_capital_ratio(summary.year5_car_pct, params.capital.car_min_pct),
        classify_ratio(
            summary.year5_lcr_pct,
            params.liquidity.lcr_min_pct,
            params.liquidity.lcr_amber_floor_pct,
        ),
        classify_ratio(
            summary.year5_nsfr_pct,
            params.liquidity.nsfr_min_pct,
            params.liquidity.nsfr_amber_floor_pct,
        ),
    )
    module_status = worst_status(*statuses)
    snapshot = current_snapshot(_build_snapshot(
        bank,
        period,
        module=MODULE_FORECAST,
        scenario_code=BASE_SCENARIO,
        facts=facts,
        active=active,
        assumptions=assumptions,
        overrides=None,
        horizon_years=5,
    ), current.source_as_of_date)
    return LiveModuleResult(
        metrics={
            **{field: str(getattr(summary, field)) for field in _SUMMARY_FIELDS},
            "assumptions": _assumptions_payload(projection.assumptions),
            "path": [_year_payload(row) for row in projection.years],
        },
        status=module_status,
        input_hash=_snapshot_hash(snapshot),
        engine_version=ENGINE_VERSION,
        findings=findings_from_validations(validations, module_status),
        source_as_of_date=current.source_as_of_date,
    )


def list_forecast_runs(
    db: Session,
    ctx: TenantContext,
    bank_id: str,
    *,
    limit: int = 25,
    offset: int = 0,
) -> ForecastRunListRead:
    bank = _get_bank_or_404(db, ctx, bank_id)
    conditions = (
        RegulatoryRun.organization_id == ctx.organization_id,
        RegulatoryRun.bank_id == bank.id,
        RegulatoryRun.module == MODULE_FORECAST,
    )
    total = db.scalar(select(func.count()).select_from(RegulatoryRun).where(*conditions)) or 0
    rows = list(
        db.execute(
            select(RegulatoryRun, BankReportingPeriod.label)
            .join(
                BankReportingPeriod,
                RegulatoryRun.reporting_period_id == BankReportingPeriod.id,
            )
            .where(
                *conditions,
                BankReportingPeriod.organization_id == ctx.organization_id,
                BankReportingPeriod.bank_id == bank.id,
            )
            .order_by(RegulatoryRun.created_at.desc(), RegulatoryRun.id.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    return ForecastRunListRead(
        bank_id=bank.id,
        runs=[_read_summary(run, label) for run, label in rows],
        total=total,
        limit=limit,
        offset=offset,
        has_more=offset + len(rows) < total,
    )


def get_forecast_run(
    db: Session, ctx: TenantContext, bank_id: str, run_id: UUID
) -> ForecastRunRead:
    bank = _get_bank_or_404(db, ctx, bank_id)
    return _read_forecast_run(db, _run_or_404(db, ctx, bank.id, run_id, module=MODULE_FORECAST))


def run_strategic_optimizer(
    db: Session, ctx: TenantContext, bank_id: str, payload: OptimizerRunCreate
) -> OptimizerResultRead:
    _require_actor(ctx)
    bank = _get_bank_or_404(db, ctx, bank_id)
    sdi_regime.require_bank_forecast_regime(db, bank)
    period = _get_period_or_404(db, ctx, bank, payload.reporting_period_id)
    facts = _load_facts(db, ctx, bank, period)
    active = _load_active_params(db, ctx, bank, period.period_end)
    presets = _load_presets(db, ctx, bank, period.period_end)
    assumptions, resolution_error = _resolve_or_defer(presets, BASE_SCENARIO, None)
    snapshot = _build_snapshot(
        bank,
        period,
        module=MODULE_OPTIMIZER,
        scenario_code=OPTIMIZER_SCENARIO,
        facts=facts,
        active=active,
        assumptions=assumptions,
        overrides=None,
    )
    run = _create_run_row(db, ctx, bank, period, MODULE_OPTIMIZER, OPTIMIZER_SCENARIO, snapshot)

    run_id = run.id
    if assumptions is None:
        _persist_failure(db, ctx, run_id, resolution_error or _missing_assumptions_error())
    else:
        try:
            engine_facts = _engine_facts_or_error(facts, period)
            params = _forecast_engine_params(active)
            constraints = OptimizerConstraints(
                car_min_pct=active.thresholds["car_min"],
                lcr_min_pct=active.thresholds["lcr_min"],
                nsfr_min_pct=active.thresholds["nsfr_min"],
            )
            result = run_optimizer(engine_facts, params, assumptions, constraints)
            _persist_optimizer_success(db, ctx, run, result, assumptions)
        except HTTPException:
            raise
        except Exception as exc:
            _persist_failure(db, ctx, run_id, _run_error(exc))
    db.expire_all()
    run = _run_or_404(db, ctx, bank.id, run_id, module=MODULE_OPTIMIZER)
    return _read_optimizer_result(run)


def run_whatif_analysis(
    db: Session, ctx: TenantContext, bank_id: str, payload: WhatIfRunCreate
) -> WhatIfResultRead:
    _require_actor(ctx)
    bank = _get_bank_or_404(db, ctx, bank_id)
    sdi_regime.require_bank_forecast_regime(db, bank)
    period = _get_period_or_404(db, ctx, bank, payload.reporting_period_id)
    facts = _load_facts(db, ctx, bank, period)
    active = _load_active_params(db, ctx, bank, period.period_end)
    presets = _load_presets(db, ctx, bank, period.period_end)
    assumptions, resolution_error = _resolve_or_defer(presets, BASE_SCENARIO, None)
    snapshot = _build_snapshot(
        bank,
        period,
        module=MODULE_WHATIF,
        scenario_code=payload.shock_code,
        facts=facts,
        active=active,
        assumptions=assumptions,
        overrides=None,
        shock=WHATIF_SHOCKS.get(payload.shock_code),
    )
    run = _create_run_row(db, ctx, bank, period, MODULE_WHATIF, payload.shock_code, snapshot)

    run_id = run.id
    if assumptions is None:
        _persist_failure(db, ctx, run_id, resolution_error or _missing_assumptions_error())
    else:
        try:
            engine_facts = _engine_facts_or_error(facts, period)
            params = _forecast_engine_params(active)
            result = run_whatif(
                payload.shock_code,
                engine_facts,
                params,
                assumptions,
                period_labels=_period_labels(period),
            )
            _persist_whatif_success(db, ctx, run, result)
        except HTTPException:
            raise
        except Exception as exc:
            _persist_failure(db, ctx, run_id, _run_error(exc))
    db.expire_all()
    run = _run_or_404(db, ctx, bank.id, run_id, module=MODULE_WHATIF)
    return _read_whatif_result(run)


def _missing_assumptions_error() -> ForecastRunError:  # pragma: no cover - defensive
    return ForecastRunError(
        "missing_parameter", "The forecast scenario assumptions could not be resolved.", None
    )


def _run_error(exc: Exception) -> ForecastRunError:  # noqa: PLR0911
    if isinstance(exc, ForecastRunError):
        return exc
    if isinstance(exc, LiquidityMissingParameterError):
        return ForecastRunError(
            "missing_parameter",
            f"No active liquidity parameter covers category '{exc.category}'.",
            {"category": exc.category},
        )
    if isinstance(exc, CapitalMissingParameterError):
        return ForecastRunError(
            "missing_parameter",
            f"No active capital parameter covers '{exc.name}'.",
            {"parameter": exc.name},
        )
    if isinstance(exc, ProjectionError):
        return ForecastRunError(exc.code, str(exc), None)
    if isinstance(exc, UnknownShockError):
        return ForecastRunError("unknown_shock", str(exc), {"shock_code": exc.shock_code})
    if isinstance(exc, LiquidityComputationError | CapitalComputationError):
        return ForecastRunError("calculation_error", str(exc), None)
    return ForecastRunError(
        "calculation_error",
        "The balance-sheet forecast could not be calculated.",
        {
            "corrective_action": (
                "Review the run inputs and retry. Contact support if it fails again."
            )
        },
    )


def _create_run_row(  # noqa: PLR0913
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    period: BankReportingPeriod,
    module: str,
    scenario_code: str,
    snapshot: dict[str, Any],
) -> RegulatoryRun:
    run = RegulatoryRun(
        organization_id=ctx.organization_id,
        bank_id=bank.id,
        reporting_period_id=period.id,
        module=module,
        scenario_code=scenario_code,
        status="queued",
        engine_version=ENGINE_VERSION,
        input_schema_version=INPUT_SCHEMA_VERSION,
        output_schema_version=OUTPUT_SCHEMA_VERSION,
        input_hash=_snapshot_hash(snapshot),
        inputs=snapshot,
        metrics={},
        # Audit D-18: WHICH governed control-plane rows produced the values in
        # ``inputs["parameters"]``. Beside the snapshot, never inside it — row
        # ids and timestamps are identity, not values, and the ``input_hash`` is
        # value-based by contract.
        parameter_provenance=regulatory_parameters.consume_parameter_provenance(db),
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
            "module": module,
            "scenario_code": scenario_code,
            "input_hash": run.input_hash,
            "engine_version": ENGINE_VERSION,
        },
    )
    db.commit()

    run.status = "running"
    run.started_at = datetime.now(UTC)
    db.commit()
    return run


def _persist_forecast_success(
    db: Session,
    ctx: TenantContext,
    run: RegulatoryRun,
    projection: ProjectionResult,
    params: ForecastParams,
) -> None:
    summary = projection.summary
    run.metrics = {
        **{field: str(getattr(summary, field)) for field in _SUMMARY_FIELDS},
        "assumptions": _assumptions_payload(projection.assumptions),
        "path": [_year_payload(row) for row in projection.years],
    }
    metric_rows: tuple[tuple[str, Decimal, Decimal | None, str], ...] = (
        ("avg_roe_pct", summary.avg_roe_pct, None, "na"),
        (
            "year5_car_pct",
            summary.year5_car_pct,
            params.capital.car_min_pct,
            classify_capital_ratio(summary.year5_car_pct, params.capital.car_min_pct),
        ),
        (
            "year5_lcr_pct",
            summary.year5_lcr_pct,
            params.liquidity.lcr_min_pct,
            classify_ratio(
                summary.year5_lcr_pct,
                params.liquidity.lcr_min_pct,
                params.liquidity.lcr_amber_floor_pct,
            ),
        ),
        (
            "year5_nsfr_pct",
            summary.year5_nsfr_pct,
            params.liquidity.nsfr_min_pct,
            classify_ratio(
                summary.year5_nsfr_pct,
                params.liquidity.nsfr_min_pct,
                params.liquidity.nsfr_amber_floor_pct,
            ),
        ),
    )
    for position, (code, value, threshold_min, metric_status) in enumerate(metric_rows, start=1):
        db.add(
            RegulatoryMetricResult(
                organization_id=run.organization_id,
                bank_id=run.bank_id,
                run_id=run.id,
                metric_code=code,
                metric_value=value,
                unit="pct",
                threshold_min=threshold_min,
                status=metric_status,
                position=position,
            )
        )
    for position, (rule_code, passed, severity, message) in enumerate(
        _validation_rows(summary, params, projection.years), start=1
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
            "module": run.module,
            "scenario_code": run.scenario_code,
            "avg_roe_pct": str(summary.avg_roe_pct),
            "year5_car_pct": str(summary.year5_car_pct),
        },
    )
    db.commit()


def _persist_optimizer_success(
    db: Session,
    ctx: TenantContext,
    run: RegulatoryRun,
    result: OptimizerResult,
    base_assumptions: ForecastAssumptions,
) -> None:
    run.metrics = {
        "candidates_evaluated": result.candidates_evaluated,
        "feasible_count": result.feasible_count,
        "binding_constraint_histogram": dict(result.binding_constraint_histogram),
        "base_assumptions": _assumptions_payload(base_assumptions),
        "top": [_candidate_payload(candidate) for candidate in result.top],
    }
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
            "module": run.module,
            "scenario_code": run.scenario_code,
            "candidates_evaluated": result.candidates_evaluated,
            "feasible_count": result.feasible_count,
        },
    )
    db.commit()


def _persist_whatif_success(
    db: Session, ctx: TenantContext, run: RegulatoryRun, result: WhatIfResult
) -> None:
    run.metrics = {
        "shock_code": result.shock_code,
        "shock": _stringified(WHATIF_SHOCKS[result.shock_code]),
        "base_assumptions": _assumptions_payload(result.base.assumptions),
        "shocked_assumptions": _assumptions_payload(result.shocked.assumptions),
        "base_summary": _summary_payload(result.base.summary),
        "shocked_summary": _summary_payload(result.shocked.summary),
        "base_path": [_year_payload(row) for row in result.base.years],
        "shocked_path": [_year_payload(row) for row in result.shocked.years],
        "deltas": [
            {
                "year": delta.year,
                "car_delta_pp": str(delta.car_delta_pp),
                "lcr_delta_pp": str(delta.lcr_delta_pp),
                "nsfr_delta_pp": str(delta.nsfr_delta_pp),
                "net_income_delta": str(delta.net_income_delta),
            }
            for delta in result.deltas
        ],
        "year5": {
            "car_pct": _comparison_payload(result.year5.car_pct),
            "lcr_pct": _comparison_payload(result.year5.lcr_pct),
            "nsfr_pct": _comparison_payload(result.year5.nsfr_pct),
            "net_income": _comparison_payload(result.year5.net_income),
        },
    }
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
            "module": run.module,
            "scenario_code": run.scenario_code,
            "year5_car_delta_pp": str(result.year5.car_pct.delta),
            "year5_net_income_delta": str(result.year5.net_income.delta),
        },
    )
    db.commit()


def _persist_failure(
    db: Session, ctx: TenantContext, run_id: UUID, error: ForecastRunError
) -> None:
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
            "module": run.module,
            "scenario_code": run.scenario_code,
            "error_code": error.code,
        },
    )
    db.commit()


#: Absolute cedi tolerance for the balance-identity check. The projection
#: rounds every scaled amount to the money quantum, so the residual between the
#: two sides is a rounding artefact, not an untied balance sheet.
_BALANCE_TOLERANCE = Decimal("0.01")


def _balance_ties_row(years: Sequence[ProjectionYear]) -> tuple[str, bool, str, str]:
    """Actually test the balance identity across the projected years (P0-14).

    This row previously hard-coded ``True`` and the sentence "Projected assets
    equal liabilities plus equity in every forecast year." Nothing was checked.
    It read as an assurance and was only ever "true" because the projection
    carries a funding plug that forces it — an assurance that a plug did its job
    is worth nothing to the reader, and worth less than nothing if the plug ever
    fails.

    What IS testable from the emitted rows, and what this now checks: the part
    of the balance sheet the projection does not restate. A ``ProjectionYear``
    exposes ``total_assets`` against ``loans + securities + cash`` on the asset
    side and ``deposits + borrowings_plug + equity`` on the funding side; the
    difference on each side is the non-scaling constant block, which is
    identical in every year by construction. So if either residual MOVES
    between years, the balance sheet has come untied — and if the year-0
    residuals disagree with the projected years', the OPENING balance sheet the
    projection was handed does not tie, which the engine never checks (its own
    guard covers years 1..N only).

    Failing here is a real finding about real data, not a restatement of a plug.
    """
    if len(years) < 2:  # noqa: PLR2004 — a single row has nothing to compare against
        return (
            "projection_balance_ties",
            True,
            "error",
            "The projection has fewer than two periods, so the balance identity has "
            "nothing to compare across years.",
        )
    asset_residuals = [
        (row, row.total_assets - (row.loans + row.securities + row.cash)) for row in years
    ]
    funding_residuals = [
        (row, row.total_assets - (row.deposits + row.borrowings_plug + row.equity))
        for row in years
    ]
    for label, residuals in (("asset", asset_residuals), ("funding", funding_residuals)):
        baseline_row, baseline = residuals[0]
        for row, residual in residuals[1:]:
            if abs(residual - baseline) > _BALANCE_TOLERANCE:
                return (
                    "projection_balance_ties",
                    False,
                    "error",
                    f"The {label} side of the balance sheet does not tie: the unexplained "
                    f"balance is {baseline} in {baseline_row.period_label} but {residual} in "
                    f"{row.period_label}. Total assets, and the components the projection "
                    "restates, must move together in every forecast year.",
                )
    return (
        "projection_balance_ties",
        True,
        "error",
        f"Checked across {len(years)} projected periods "
        f"({years[0].period_label} to {years[-1].period_label}): total assets stay tied to "
        "both the asset components and to deposits, borrowings and equity in every year.",
    )


def _validation_rows(
    summary: ProjectionSummary,
    params: ForecastParams,
    years: Sequence[ProjectionYear] = (),
) -> tuple[tuple[str, bool, str, str], ...]:
    checks = (
        ("year5_car_above_minimum", "CAR", summary.year5_car_pct, params.capital.car_min_pct),
        ("year5_lcr_above_minimum", "LCR", summary.year5_lcr_pct, params.liquidity.lcr_min_pct),
        (
            "year5_nsfr_above_minimum",
            "NSFR",
            summary.year5_nsfr_pct,
            params.liquidity.nsfr_min_pct,
        ),
    )
    rows: list[tuple[str, bool, str, str]] = [_balance_ties_row(years)]
    for rule_code, label, value, minimum in checks:
        passed = value >= minimum
        rows.append(
            (
                rule_code,
                passed,
                "error",
                f"The year-5 {label} of {_pct_text(value)}% is "
                + ("at or above" if passed else "below")
                + f" the {_pct_text(minimum)}% regulatory minimum.",
            )
        )
    return tuple(rows)


def _resolve_or_defer(
    presets: dict[str, dict[str, Decimal]],
    scenario_code: str,
    overrides: ForecastAssumptionsUpdate | None,
) -> tuple[ForecastAssumptions | None, ForecastRunError | None]:
    try:
        return _resolve_assumptions(presets, scenario_code, overrides), None
    except ForecastRunError as exc:
        return None, exc


def _resolve_assumptions(
    presets: dict[str, dict[str, Decimal]],
    scenario_code: str,
    overrides: ForecastAssumptionsUpdate | None,
) -> ForecastAssumptions:
    preset_code = BASE_SCENARIO if scenario_code == CUSTOM_SCENARIO else scenario_code
    preset = presets.get(preset_code, {})
    values: dict[str, Decimal] = {key: preset[key] for key in ASSUMPTION_KEYS if key in preset}
    if overrides is not None:
        for key in _ALL_ASSUMPTION_KEYS:
            override = getattr(overrides, key)
            if override is not None:
                values[key] = override
    missing = [key for key in ASSUMPTION_KEYS if key not in values]
    if missing:
        raise ForecastRunError(
            "missing_parameter",
            f"The '{preset_code}' forecast scenario parameters do not cover: "
            + ", ".join(missing)
            + ".",
            {"scenario_code": preset_code, "assumption_keys": missing},
        )
    return ForecastAssumptions(**values)


def _engine_facts_or_error(
    facts: Sequence[FinancialFactRow], period: BankReportingPeriod
) -> tuple[ForecastFact, ...]:
    if not facts:
        raise ForecastRunError(
            "financial_facts_missing",
            "The reporting period has no financial facts to analyze.",
            {"reporting_period_id": str(period.id)},
        )
    return tuple(_to_engine_fact(fact) for fact in facts)


def _to_engine_fact(fact: FinancialFactRow) -> ForecastFact:
    return ForecastFact(
        fact_group=fact.fact_group,
        category=fact.category,
        amount=Decimal(str(fact.amount)),
        risk_weight_code=fact.risk_weight_code,
        hqla_level=fact.hqla_level,
        ccf_pct=Decimal(str(fact.ccf_pct)) if fact.ccf_pct is not None else None,
        income_year=fact.income_year,
        capital_tier=fact.capital_tier,
        is_deduction=fact.is_deduction,
        side=fact.attributes.get("side"),
        cash_derived=fact.attributes.get("source") == "cash",
    )


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
                BankFinancialFact.fact_group.in_(_FORECAST_FACT_GROUPS),
            )
            .order_by(BankFinancialFact.fact_group, BankFinancialFact.category)
        )
    )


def _load_active_params(
    db: Session, ctx: TenantContext, bank: Bank, as_of: date
) -> _ActiveForecastParams:
    runoff_rows = get_active_params(
        db, ctx.organization_id, bank.jurisdiction_code, ParamLcrRunoffRate, as_of
    )
    nsfr_rows = get_active_params(
        db, ctx.organization_id, bank.jurisdiction_code, ParamNsfrWeight, as_of
    )
    weight_rows = get_active_params(
        db, ctx.organization_id, bank.jurisdiction_code, ParamRiskWeight, as_of
    )
    threshold_rows = get_active_params(
        db, ctx.organization_id, bank.jurisdiction_code, ParamCapitalThreshold, as_of
    )
    # Same two registers ``regulatory_capital._load_active_params`` reads, same
    # precedence (code defaults overlaid by the effective-dated register for CRM;
    # register-only for ECL). Resolving them differently here would be the
    # divergence in another form.
    crm_rows = get_active_params(
        db, ctx.organization_id, bank.jurisdiction_code, ParamCrmHaircut, as_of
    )
    ecl_rows = get_active_params(
        db, ctx.organization_id, bank.jurisdiction_code, ParamEclAssumption, as_of
    )
    crm_haircuts = dict(DEFAULT_CRM_HAIRCUTS)
    crm_haircuts.update({row.collateral_class: Decimal(str(row.haircut_pct)) for row in crm_rows})
    outflow_rates: dict[str, Decimal] = {}
    inflow_rates: dict[str, Decimal] = {}
    for row in runoff_rows:
        target = outflow_rates if row.flow_direction == "outflow" else inflow_rates
        target[row.category] = Decimal(str(row.rate_pct))
    asf_weights: dict[str, Decimal] = {}
    rsf_weights: dict[str, Decimal] = {}
    for row in nsfr_rows:
        target = asf_weights if row.side == "asf" else rsf_weights
        target[row.category] = Decimal(str(row.weight_pct))
    # Tighten-only guard (QA audit 2026-08-20 P1-5). Forecasting reads the SAME
    # board register as ``regulatory_capital``, which has clamped ``car_min``
    # against the control-plane regulatory floor since Phase E — this loader did
    # not, so one module projected against the regulatory minimum and the other
    # against a weaker board value for the same code. Both now go through the one
    # generalised clamp; codes with no seeded control-plane row are untouched.
    thresholds = regulatory_parameters.clamp_overrides(
        db,
        bank,
        {row.threshold_code: Decimal(str(row.value_pct)) for row in threshold_rows},
        as_of=as_of,
    ).values
    return _ActiveForecastParams(
        hqla=regulatory_parameters.resolve_hqla_parameters(db, bank, as_of=as_of),
        outflow_rates=outflow_rates,
        inflow_rates=inflow_rates,
        asf_weights=asf_weights,
        rsf_weights=rsf_weights,
        risk_weights={row.risk_weight_code: Decimal(str(row.weight_pct)) for row in weight_rows},
        thresholds=thresholds,
        crm_haircuts=crm_haircuts,
        ecl_assumptions=tuple(
            EclAssumption(
                segment=row.segment,
                stage=row.stage,
                pd_pct=Decimal(str(row.pd_pct)),
                lgd_pct=Decimal(str(row.lgd_pct)),
            )
            for row in ecl_rows
        ),
    )


def _load_presets(
    db: Session, ctx: TenantContext, bank: Bank, as_of: date
) -> dict[str, dict[str, Decimal]]:
    rows = get_active_params(
        db, ctx.organization_id, bank.jurisdiction_code, ParamStressShock, as_of
    )
    presets: dict[str, dict[str, Decimal]] = {}
    for row in rows:
        if row.module != MODULE_FORECAST:
            continue
        presets.setdefault(row.scenario_code, {})[row.shock_key] = Decimal(str(row.shock_value))
    return presets


def _engine_params(active: _ActiveForecastParams) -> ForecastParams:
    missing = sorted(
        {
            *(code for code in _REQUIRED_LIQUIDITY_THRESHOLDS if code not in active.thresholds),
            *(code for code in _REQUIRED_CAPITAL_THRESHOLDS if code not in active.thresholds),
        }
    )
    if missing:
        raise ForecastRunError(
            "missing_parameter",
            "Required threshold parameters are not configured: " + ", ".join(missing) + ".",
            {"threshold_codes": missing},
        )
    thresholds = active.thresholds
    amber_floor = thresholds["lcr_amber_floor"]
    liquidity = LiquidityParams(
        outflow_rates=active.outflow_rates,
        inflow_rates=active.inflow_rates,
        asf_weights=active.asf_weights,
        rsf_weights=active.rsf_weights,
        inflow_cap_pct=thresholds["lcr_inflow_cap_pct"],
        lcr_min_pct=thresholds["lcr_min"],
        lcr_amber_floor_pct=amber_floor,
        nsfr_min_pct=thresholds["nsfr_min"],
        nsfr_amber_floor_pct=amber_floor,
        hqla_haircut_pct=active.hqla.haircut_pct,
        hqla_level2_cap_pct=active.hqla.level2_cap_pct,
        hqla_level2b_cap_pct=active.hqla.level2b_cap_pct,
    )
    capital = CapitalParams(
        risk_weights=active.risk_weights,
        bia_alpha_pct=thresholds["bia_alpha_pct"],
        fx_charge_pct=thresholds["fx_charge_pct"],
        rwa_multiplier_pct=thresholds["rwa_multiplier"],
        tier2_gp_cap_pct_credit_rwa=thresholds["tier2_gp_cap_pct_credit_rwa"],
        cet1_min_pct=thresholds["cet1_min"],
        tier1_min_pct=thresholds["tier1_min"],
        car_min_pct=thresholds["car_min"],
        leverage_min_pct=thresholds["leverage_min"],
        car_early_warning_pct=thresholds["car_early_warning"],
        car_critical_pct=thresholds["car_critical"],
    )
    return ForecastParams(liquidity=liquidity, capital=capital)


def _forecast_engine_params(active: _ActiveForecastParams) -> ForecastParams:
    """The parameter set the projection actually runs on.

    ``_engine_params`` resolves the threshold/weight registers. This layer adds
    the two Phase-2 capital inputs that make the projection's capital arithmetic
    identical to the capital run's: the CRM haircut schedule (credit RWA) and
    the IFRS 9 assumption register (the general-provisions Tier 2 override).
    ``crm_haircuts`` always carries the Basel code defaults, so this normally
    layers; the early return covers a caller that supplies neither register.
    Either way the figures only move for a book that actually holds
    ``crm_collateral`` facts or a configured IFRS 9 register — the capital run
    behaves identically.

    It is a separate function rather than extra lines inside ``_engine_params``
    so the threshold-resolution contract stays in one place and this parity
    layer stays reviewable on its own.
    """
    params = _engine_params(active)
    if not active.crm_haircuts and not active.ecl_assumptions:
        return params
    return replace(
        params,
        capital=replace(params.capital, crm_haircuts=active.crm_haircuts),
        ecl_assumptions=active.ecl_assumptions,
    )


def _period_labels(period: BankReportingPeriod, years: int = PROJECTION_YEARS) -> list[str]:
    end = period.period_end
    return [period.label] + [
        f"{end.year + offset:04d}-{end.month:02d}" for offset in range(1, years + 1)
    ]


def _live_period_labels(as_of_date: date, years: int = PROJECTION_YEARS) -> list[str]:
    return [f"{as_of_date.year:04d}-{as_of_date.month:02d}"] + [
        f"{as_of_date.year + offset:04d}-{as_of_date.month:02d}"
        for offset in range(1, years + 1)
    ]


def _build_snapshot(  # noqa: PLR0913
    bank: Bank,
    period: BankReportingPeriod,
    *,
    module: str,
    scenario_code: str,
    facts: Sequence[FinancialFactRow],
    active: _ActiveForecastParams,
    assumptions: ForecastAssumptions | None,
    overrides: ForecastAssumptionsUpdate | None,
    shock: dict[str, Decimal] | None = None,
    horizon_years: int = PROJECTION_YEARS,
) -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "schema_version": INPUT_SCHEMA_VERSION,
        "module": module,
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
                    "risk_weight_code": fact.risk_weight_code,
                    "hqla_level": fact.hqla_level,
                    "ccf_pct": str(fact.ccf_pct) if fact.ccf_pct is not None else None,
                    "income_year": fact.income_year,
                    "capital_tier": fact.capital_tier,
                    "is_deduction": fact.is_deduction,
                    "side": fact.attributes.get("side"),
                    "cash_derived": fact.attributes.get("source") == "cash",
                }
                for fact in facts
            ),
            key=lambda entry: json.dumps(entry, sort_keys=True),
        ),
        "parameters": _snapshot_parameters(
            active, consumed_hqla_levels=_consumed_hqla_levels(facts)
        ),
        "assumption_overrides": (
            _stringified(
                {
                    key: value
                    for key in _ALL_ASSUMPTION_KEYS
                    if (value := getattr(overrides, key)) is not None
                }
            )
            if overrides is not None
            else None
        ),
        "assumptions": _assumptions_payload(assumptions) if assumptions is not None else None,
    }
    if shock is not None:
        snapshot["shock"] = _stringified(shock)
    # Provenance for non-default horizons only: the default-horizon snapshot
    # stays byte-identical to every pre-horizon run, so unchanged inputs keep
    # reproducing the same immutable input_hash across the upgrade.
    if horizon_years != PROJECTION_YEARS:
        snapshot["horizon_years"] = horizon_years
    return snapshot


def _assumptions_payload(assumptions: ForecastAssumptions) -> dict[str, str]:
    return {key: str(getattr(assumptions, key)) for key in _ALL_ASSUMPTION_KEYS}


def _year_payload(row: ProjectionYear) -> dict[str, Any]:
    payload: dict[str, Any] = {"year": row.year, "period_label": row.period_label}
    for field in _PATH_MONEY_FIELDS:
        payload[field] = str(getattr(row, field))
    payload["roe_pct"] = str(row.roe_pct) if row.roe_pct is not None else None
    for field in _PATH_RATIO_FIELDS:
        payload[field] = str(getattr(row, field))
    return payload


def _summary_payload(summary: ProjectionSummary) -> dict[str, str]:
    return {field: str(getattr(summary, field)) for field in _SUMMARY_FIELDS}


def _candidate_payload(candidate: OptimizerCandidateResult) -> dict[str, Any]:
    return {
        "decision": _decision_payload(candidate.decision),
        "summary": _summary_payload(candidate.summary),
        "constraint_status": [
            {
                "constraint": item.constraint,
                "minimum_pct": str(item.minimum_pct),
                "observed_min_pct": str(item.observed_min_pct),
                "passed": item.passed,
            }
            for item in candidate.constraint_status
        ],
        "feasible": candidate.feasible,
    }


def _decision_payload(decision: OptimizerDecision) -> dict[str, Any]:
    return {
        "loan_growth_pct": str(decision.loan_growth_pct),
        "securities_shift_pp": str(decision.securities_shift_pp),
        "deposit_premium_bps": decision.deposit_premium_bps,
        "dividend_payout_pct": str(decision.dividend_payout_pct),
        "deposit_growth_delta_pct": str(decision.deposit_growth_delta_pct),
        "nim_delta_pct": str(decision.nim_delta_pct),
    }


def _comparison_payload(comparison: WhatIfMetricComparison) -> dict[str, str]:
    return {
        "base": str(comparison.base),
        "shocked": str(comparison.shocked),
        "delta": str(comparison.delta),
    }


def _read_forecast_run(db: Session, run: RegulatoryRun) -> ForecastRunRead:
    metric_results = list(
        db.scalars(
            select(RegulatoryMetricResult)
            .where(
                RegulatoryMetricResult.run_id == run.id,
                RegulatoryMetricResult.organization_id == run.organization_id,
                RegulatoryMetricResult.bank_id == run.bank_id,
            )
            .order_by(RegulatoryMetricResult.position)
        )
    )
    validations = list(
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
    return ForecastRunRead(
        id=run.id,
        organization_id=run.organization_id,
        bank_id=run.bank_id,
        reporting_period_id=run.reporting_period_id,
        module="forecast",
        scenario_code=run.scenario_code,  # type: ignore[arg-type]
        status=run.status,  # type: ignore[arg-type]
        engine_version=run.engine_version,
        input_schema_version=run.input_schema_version,
        output_schema_version=run.output_schema_version,
        input_hash=run.input_hash,
        inputs=run.inputs,
        assumptions=_assumptions_read(run.inputs.get("assumptions")),
        path=_path_read(run.metrics.get("path")),
        summary=_summary_read(run.metrics),
        metric_results=[RegulatoryMetricResultRead.model_validate(item) for item in metric_results],
        validations=[RegulatoryValidationRead.model_validate(item) for item in validations],
        error=_error_read(run),
        started_at=run.started_at,
        completed_at=run.completed_at,
        created_by=run.created_by,
        created_at=run.created_at,
        updated_at=run.updated_at,
    )


def _read_summary(run: RegulatoryRun, period_label: str) -> ForecastRunSummaryRead:
    metrics = run.metrics
    return ForecastRunSummaryRead(
        id=run.id,
        scenario_code=run.scenario_code,  # type: ignore[arg-type]
        status=run.status,  # type: ignore[arg-type]
        reporting_period_id=run.reporting_period_id,
        period_label=period_label,
        input_hash=run.input_hash,
        avg_roe_pct=_optional_decimal(metrics.get("avg_roe_pct")),
        year5_car_pct=_optional_decimal(metrics.get("year5_car_pct")),
        year5_lcr_pct=_optional_decimal(metrics.get("year5_lcr_pct")),
        year5_nsfr_pct=_optional_decimal(metrics.get("year5_nsfr_pct")),
        error=_error_read(run),
        created_at=run.created_at,
    )


def _read_optimizer_result(run: RegulatoryRun) -> OptimizerResultRead:
    metrics = run.metrics
    return OptimizerResultRead(
        run_id=run.id,
        bank_id=run.bank_id,
        reporting_period_id=run.reporting_period_id,
        scenario_code="constrained_search",
        status=run.status,  # type: ignore[arg-type]
        input_hash=run.input_hash,
        base_assumptions=_assumptions_read(metrics.get("base_assumptions")),
        candidates_evaluated=int(metrics.get("candidates_evaluated", 0)),
        feasible_count=int(metrics.get("feasible_count", 0)),
        top=[_candidate_read(item) for item in metrics.get("top", [])],
        binding_constraint_histogram={
            str(key): int(value)
            for key, value in metrics.get("binding_constraint_histogram", {}).items()
        },
        error=_error_read(run),
        created_at=run.created_at,
    )


def _read_whatif_result(run: RegulatoryRun) -> WhatIfResultRead:
    metrics = run.metrics
    year5 = metrics.get("year5")
    return WhatIfResultRead(
        run_id=run.id,
        bank_id=run.bank_id,
        reporting_period_id=run.reporting_period_id,
        shock_code=run.scenario_code,  # type: ignore[arg-type]
        status=run.status,  # type: ignore[arg-type]
        input_hash=run.input_hash,
        base_assumptions=_assumptions_read(metrics.get("base_assumptions")),
        shocked_assumptions=_assumptions_read(metrics.get("shocked_assumptions")),
        base_path=_path_read(metrics.get("base_path")),
        shocked_path=_path_read(metrics.get("shocked_path")),
        base_summary=_summary_read(metrics.get("base_summary") or {}),
        shocked_summary=_summary_read(metrics.get("shocked_summary") or {}),
        deltas=[
            WhatIfYearDeltaRead(
                year=int(item["year"]),
                car_delta_pp=Decimal(str(item["car_delta_pp"])),
                lcr_delta_pp=Decimal(str(item["lcr_delta_pp"])),
                nsfr_delta_pp=Decimal(str(item["nsfr_delta_pp"])),
                net_income_delta=Decimal(str(item["net_income_delta"])),
            )
            for item in metrics.get("deltas", [])
        ],
        year5=(
            WhatIfYear5ComparisonRead(
                car_pct=_comparison_read(year5["car_pct"]),
                lcr_pct=_comparison_read(year5["lcr_pct"]),
                nsfr_pct=_comparison_read(year5["nsfr_pct"]),
                net_income=_comparison_read(year5["net_income"]),
            )
            if year5 is not None
            else None
        ),
        error=_error_read(run),
        created_at=run.created_at,
    )


def _candidate_read(item: dict[str, Any]) -> OptimizerCandidateRead:
    decision = item["decision"]
    return OptimizerCandidateRead(
        decision=OptimizerDecisionRead(
            loan_growth_pct=Decimal(str(decision["loan_growth_pct"])),
            securities_shift_pp=Decimal(str(decision["securities_shift_pp"])),
            deposit_premium_bps=int(decision["deposit_premium_bps"]),
            dividend_payout_pct=Decimal(str(decision["dividend_payout_pct"])),
            deposit_growth_delta_pct=Decimal(str(decision["deposit_growth_delta_pct"])),
            nim_delta_pct=Decimal(str(decision["nim_delta_pct"])),
        ),
        summary=_summary_read_required(item["summary"]),
        constraint_status=[
            OptimizerConstraintStatusRead(
                constraint=status_item["constraint"],
                minimum_pct=Decimal(str(status_item["minimum_pct"])),
                observed_min_pct=Decimal(str(status_item["observed_min_pct"])),
                passed=bool(status_item["passed"]),
            )
            for status_item in item["constraint_status"]
        ],
        feasible=bool(item["feasible"]),
    )


def _assumptions_read(payload: Any) -> ForecastAssumptionsRead | None:
    if not isinstance(payload, dict):
        return None
    return ForecastAssumptionsRead(
        **{key: Decimal(str(payload[key])) for key in _ALL_ASSUMPTION_KEYS}
    )


def _path_read(payload: Any) -> list[ProjectionYearRead]:
    if not isinstance(payload, list):
        return []
    rows: list[ProjectionYearRead] = []
    for item in payload:
        values: dict[str, Any] = {
            "year": int(item["year"]),
            "period_label": str(item["period_label"]),
            "roe_pct": Decimal(str(item["roe_pct"])) if item.get("roe_pct") is not None else None,
        }
        for field in (*_PATH_MONEY_FIELDS, *_PATH_RATIO_FIELDS):
            values[field] = Decimal(str(item[field]))
        rows.append(ProjectionYearRead(**values))
    return rows


def _summary_read(metrics: dict[str, Any]) -> ProjectionSummaryRead | None:
    if not all(field in metrics for field in _SUMMARY_FIELDS):
        return None
    return _summary_read_required(metrics)


def _summary_read_required(metrics: dict[str, Any]) -> ProjectionSummaryRead:
    return ProjectionSummaryRead(
        **{field: Decimal(str(metrics[field])) for field in _SUMMARY_FIELDS}
    )


def _comparison_read(payload: dict[str, Any]) -> WhatIfMetricComparisonRead:
    return WhatIfMetricComparisonRead(
        base=Decimal(str(payload["base"])),
        shocked=Decimal(str(payload["shocked"])),
        delta=Decimal(str(payload["delta"])),
    )


def _optional_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


def _error_read(run: RegulatoryRun) -> RegulatoryRunErrorRead | None:
    if not run.error_code or not run.error_message:
        return None
    return RegulatoryRunErrorRead(
        code=run.error_code, message=run.error_message, details=run.error_details
    )


def _consumed_hqla_levels(facts: Sequence[FinancialFactRow]) -> set[str]:
    """The Basel levels the projected LCR will actually charge a haircut for.

    The forecast engine projects each securities fact forward carrying the level
    its base fact was derived with and hands the result to the SAME
    ``compute_lcr``, so the levels consumed downstream are the levels present
    here. Kept deliberately identical to
    ``regulatory_liquidity._consumed_hqla_levels`` — see that docstring for why
    the L1 rate counts as consumed (forensic re-audit 2026-08-22 D-7).
    """
    levels = {
        (fact.hqla_level or "").strip().upper()
        for fact in facts
        if fact.fact_group == FACT_GROUP_SECURITIES and fact.hqla_level is not None
    }
    return levels & set(HQLA_LEVELS)


def _snapshot_parameters(
    active: _ActiveForecastParams, *, consumed_hqla_levels: set[str] | None = None
) -> dict[str, Any]:
    """Governed inputs recorded in the hashed snapshot.

    Mirrors ``regulatory_capital._snapshot_parameters``: the CRM and ECL blocks
    join only when a register actually changes the arithmetic, so a book that has
    configured neither hashes exactly as it did before they became forecast
    inputs (value-based discipline — the hash tracks what was consumed, not what
    the loader happened to query).
    """
    parameters: dict[str, Any] = {
        "outflow_runoff_rates_pct": _stringified(active.outflow_rates),
        "inflow_rates_pct": _stringified(active.inflow_rates),
        "asf_weights_pct": _stringified(active.asf_weights),
        "rsf_weights_pct": _stringified(active.rsf_weights),
        "risk_weights_pct": _stringified(active.risk_weights),
        "thresholds_pct": _stringified(active.thresholds),
    }
    # Same join-only-when-consumed rule for the Basel HQLA haircuts and Level-2
    # caps (enterprise audit P0-8). Forensic re-audit 2026-08-22 D-7: the
    # projected LCR calls ``_hqla_haircut(params, "L1")`` for every Level-1
    # asset and refuses when it is unresolved, so the governed L1 rate is
    # CONSUMED by every book holding any HQLA — it belongs in the hash. The
    # Level-2 caps remain conditional because only a Level-2 holding resolves
    # them.
    consumed = consumed_hqla_levels or set()
    if consumed:
        parameters["hqla_haircuts_pct"] = _stringified(
            {level: rate for level, rate in active.hqla.haircut_pct.items() if level in consumed}
        )
    if consumed - {HQLA_LEVEL_1}:
        parameters["hqla_caps_pct"] = _stringified(
            {
                code: value
                for code, value in (
                    (regulatory_parameters.HQLA_LEVEL2_CAP_CODE, active.hqla.level2_cap_pct),
                    (regulatory_parameters.HQLA_LEVEL2B_CAP_CODE, active.hqla.level2b_cap_pct),
                )
                if value is not None
            }
        )
    configured_crm = {
        key: value
        for key, value in active.crm_haircuts.items()
        if DEFAULT_CRM_HAIRCUTS.get(key) != value
    }
    if configured_crm:
        parameters["crm_haircuts_pct"] = _stringified(configured_crm)
    if active.ecl_assumptions:
        parameters["ecl_assumptions"] = sorted(
            (
                {
                    "segment": row.segment,
                    "stage": row.stage,
                    "pd_pct": str(row.pd_pct),
                    "lgd_pct": str(row.lgd_pct),
                }
                for row in active.ecl_assumptions
            ),
            key=lambda entry: (entry["segment"], entry["stage"]),
        )
    return parameters


def _stringified(values: dict[str, Decimal]) -> dict[str, str]:
    return {key: str(value) for key, value in sorted(values.items())}


def _snapshot_hash(snapshot: dict[str, Any]) -> str:
    payload = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def _pct_text(value: Decimal) -> str:
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def _run_or_404(
    db: Session, ctx: TenantContext, bank_id: str, run_id: UUID, *, module: str | None = None
) -> RegulatoryRun:
    conditions = [
        RegulatoryRun.id == run_id,
        RegulatoryRun.organization_id == ctx.organization_id,
        RegulatoryRun.bank_id == bank_id,
    ]
    if module is not None:
        conditions.append(RegulatoryRun.module == module)
    run = db.scalar(select(RegulatoryRun).where(*conditions))
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Regulatory run not found."
        )
    return run


def _latest_period_end(db: Session, ctx: TenantContext, bank: Bank) -> date | None:
    return db.scalar(
        select(func.max(BankReportingPeriod.period_end)).where(
            BankReportingPeriod.organization_id == ctx.organization_id,
            BankReportingPeriod.bank_id == bank.id,
        )
    )


def _get_bank_or_404(db: Session, ctx: TenantContext, bank_id: str) -> Bank:
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
