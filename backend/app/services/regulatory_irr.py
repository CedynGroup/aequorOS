"""Regulatory IRRBB runs: gap / duration / EVE / EaR orchestration and dashboard.

Follows the immutable calculation-run lifecycle established by
``app.services.regulatory_capital``: runs commit ``queued`` and ``running``
before executing, persist the full canonical input snapshot with a SHA-256
``input_hash``, and record failures as data (named error codes) rather than
HTTP 500s. The arithmetic itself lives in the pure engine at
``app.domain.irr.engine``.

Every IRR run computes the complete banking-book analysis — repricing gap,
duration gap, base EVE with all six Basel scenarios, and parallel ±200 bp
earnings-at-risk. ``scenario_code`` tags which scenario the run highlights; the
stored metrics and line items are the full analysis so any run is a
self-contained snapshot. Tier 1 capital is read from the capital-component
facts at run time as the denominator for the ΔEVE limit but is deliberately
kept OUT of the input hash, so the IRR hash scopes reproducibility to the
interest-rate positions, hedges, and IRR parameters.
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
from app.domain.irr.engine import (
    BASE_CURVE_SCENARIO,
    EAR_DOWN_BP,
    EAR_UP_BP,
    IRR_SCENARIO_CODES,
    DurationResult,
    EveResult,
    GapResult,
    IrrComputationError,
    IrrPosition,
    MissingParameterError,
    UnsupportedShockError,
    classify_eve_change,
    compute_duration,
    compute_ear,
    compute_gap,
    compute_nii,
    ear_line_item,
    run_irr_scenarios,
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
from app.schemas.regulatory_irr import (
    IrrDashboardRead,
    IrrEveScenarioRead,
    IrrGapBucketRead,
    IrrMetricsRead,
    IrrScenarioBatchCreate,
    IrrTrendPointRead,
    IrrValidationRead,
)
from app.schemas.regulatory_liquidity import (
    RegulatoryRunBatchRead,
    RegulatoryRunCreate,
    RegulatoryRunRead,
)
from app.services.audit import record_event
from app.services.params import get_active_params
from app.services.regulatory_liquidity import get_regulatory_run

ENGINE_VERSION = "regulatory-irr-v1.0.0"
INPUT_SCHEMA_VERSION = "bank-facts-v1"
OUTPUT_SCHEMA_VERSION = "irr-metrics-v1"
MODULE_IRR = "irr"
BASELINE_SCENARIO = "baseline"
IRR_RUN_SCENARIO_CODES = (BASELINE_SCENARIO, *IRR_SCENARIO_CODES)

EVE_LIMIT_THRESHOLD = "eve_tier1_limit_pct"
NII_LIMIT_THRESHOLD = "irr_nii_limit_pct"

# Only these fact groups participate in the IRR module; keeping the snapshot
# scoped to them makes the input hash insensitive to unrelated fact edits.
_IRR_FACT_GROUPS = ("irr_position", "irr_swap")
_CAPITAL_COMPONENT_GROUP = "capital_component"

_ZERO = Decimal("0")
_HUNDRED = Decimal("100")


class IrrRunError(Exception):
    """Domain input failure persisted onto the run instead of raising HTTP 500."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


@dataclass(frozen=True)
class _IrrParams:
    curve: dict[Decimal, Decimal]
    scenario_shocks: dict[str, dict[str, Decimal]]
    eve_limit_pct: Decimal
    nii_limit_pct: Decimal


@dataclass(frozen=True)
class _IrrAnalysis:
    gap: GapResult
    duration: DurationResult
    eve: EveResult
    ear_up: Decimal
    ear_down: Decimal
    nii_base: Decimal
    eve_limit_pct: Decimal
    nii_limit_pct: Decimal


def create_irr_run(
    db: Session, ctx: TenantContext, bank_id: UUID, payload: RegulatoryRunCreate
) -> RegulatoryRunRead:
    _require_actor(ctx)
    bank = _get_bank_or_404(db, ctx, bank_id)
    period = _get_period_or_404(db, ctx, bank, payload.reporting_period_id)
    return _create_and_execute(db, ctx, bank, period, payload.scenario_code)


def run_all_irr_scenarios(
    db: Session, ctx: TenantContext, bank_id: UUID, payload: IrrScenarioBatchCreate
) -> RegulatoryRunBatchRead:
    _require_actor(ctx)
    bank = _get_bank_or_404(db, ctx, bank_id)
    period = _get_period_or_404(db, ctx, bank, payload.reporting_period_id)
    runs = [
        _create_and_execute(db, ctx, bank, period, scenario_code)
        for scenario_code in IRR_RUN_SCENARIO_CODES
    ]
    return RegulatoryRunBatchRead(bank_id=bank.id, reporting_period_id=period.id, runs=runs)


def get_irr_dashboard(
    db: Session, ctx: TenantContext, bank_id: UUID, reporting_period_id: UUID | None = None
) -> IrrDashboardRead:
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
        gap_table = _gap_table_from_run(latest_run)
        eve_scenarios = _eve_scenarios_from_run(latest_run)
        validations = [
            IrrValidationRead(
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
        gap_table = _gap_table_from_analysis(analysis)
        eve_scenarios = _eve_scenarios_from_analysis(analysis)
        validations = [
            IrrValidationRead(
                rule_code=rule_code,
                passed=passed,
                severity=severity,  # type: ignore[arg-type]
                message=message,
            )
            for rule_code, passed, severity, message in _validation_rows(analysis)
        ]
        stored = False

    return IrrDashboardRead(
        bank=BankRead.model_validate(bank, from_attributes=True),
        period=BankReportingPeriodRead.model_validate(period, from_attributes=True),
        stored=stored,
        latest_run_id=latest_run.id if latest_run is not None else None,
        metrics=metrics,
        gap_table=gap_table,
        eve_scenarios=eve_scenarios,
        trend=_build_trend(db, ctx, bank, periods),
        validations=validations,
    )


def _create_and_execute(
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    period: BankReportingPeriod,
    scenario_code: str,
) -> RegulatoryRunRead:
    facts = _load_facts(db, ctx, bank, period)
    active = _load_irr_params_or_none(db, ctx, bank, period.period_end)
    snapshot = _build_snapshot(bank, period, scenario_code, facts, active)

    run = RegulatoryRun(
        organization_id=ctx.organization_id,
        bank_id=bank.id,
        reporting_period_id=period.id,
        module=MODULE_IRR,
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
            "module": MODULE_IRR,
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
    except IrrRunError as exc:
        _persist_failure(db, ctx, run_id, exc)
    except MissingParameterError as exc:
        _persist_failure(
            db,
            ctx,
            run_id,
            IrrRunError(
                "missing_parameter",
                f"No active IRR parameter covers '{exc.name}'.",
                {"parameter": exc.name},
            ),
        )
    except UnsupportedShockError as exc:
        _persist_failure(
            db,
            ctx,
            run_id,
            IrrRunError(
                "unsupported_shock",
                str(exc),
                {"scenario_code": exc.scenario_code, "shock_key": exc.shock_key},
            ),
        )
    except IrrComputationError as exc:
        _persist_failure(db, ctx, run_id, IrrRunError("calculation_error", str(exc), None))
    except HTTPException:
        raise
    except Exception:
        _persist_failure(
            db,
            ctx,
            run_id,
            IrrRunError(
                "calculation_error",
                "The IRR metrics could not be calculated.",
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
    active: _IrrParams | None,
) -> _IrrAnalysis:
    if not facts:
        raise IrrRunError(
            "financial_facts_missing",
            "The reporting period has no interest-rate positions to analyze.",
            {"reporting_period_id": str(period.id)},
        )
    if active is None:
        raise IrrRunError(
            "missing_parameter",
            "Required IRR parameters (base curve, scenario shocks, or limits) are not configured.",
            None,
        )
    tier1 = _load_tier1(db, ctx, bank, period)
    if tier1 <= _ZERO:
        raise IrrRunError(
            "missing_parameter",
            "Tier 1 capital could not be derived from the capital-component facts.",
            None,
        )
    positions = _positions_from_facts(facts, active.curve)
    gap = compute_gap(positions)
    duration = compute_duration(positions, active.curve)
    eve = run_irr_scenarios(
        positions, active.curve, active.scenario_shocks, tier1, active.eve_limit_pct
    )
    return _IrrAnalysis(
        gap=gap,
        duration=duration,
        eve=eve,
        ear_up=compute_ear(gap, EAR_UP_BP),
        ear_down=compute_ear(gap, EAR_DOWN_BP),
        nii_base=compute_nii(positions),
        eve_limit_pct=active.eve_limit_pct,
        nii_limit_pct=active.nii_limit_pct,
    )


def _persist_success(
    db: Session, ctx: TenantContext, run: RegulatoryRun, analysis: _IrrAnalysis
) -> None:
    run.metrics = _metrics_payload(analysis)

    eve_status = classify_eve_change(
        abs(analysis.eve.worst_delta_eve_pct_tier1), analysis.eve_limit_pct
    )
    metric_rows: list[tuple[str, Decimal, str, Decimal | None, str]] = [
        (
            "worst_eve_change_pct_tier1",
            analysis.eve.worst_delta_eve_pct_tier1,
            "pct",
            analysis.eve_limit_pct,
            eve_status,
        ),
        ("duration_gap", analysis.duration.duration_gap, "years", None, "na"),
        ("asset_duration", analysis.duration.asset_modified, "years", None, "na"),
        ("liability_duration", analysis.duration.liability_modified, "years", None, "na"),
        ("cumulative_12m_gap_ghs", analysis.gap.cumulative_12m_gap, "ghs", None, "na"),
        ("eve_base_ghs", analysis.eve.base_eve, "ghs", None, "na"),
        ("ear_up_200_ghs", analysis.ear_up, "ghs", None, "na"),
        ("ear_down_200_ghs", analysis.ear_down, "ghs", None, "na"),
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
        *analysis.gap.line_items,
        *analysis.eve.line_items,
        ear_line_item(EAR_UP_BP, analysis.ear_up),
        ear_line_item(EAR_DOWN_BP, analysis.ear_down),
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
            "worst_eve_change_pct_tier1": str(analysis.eve.worst_delta_eve_pct_tier1),
            "duration_gap": str(analysis.duration.duration_gap),
        },
    )
    db.commit()


def _persist_failure(db: Session, ctx: TenantContext, run_id: UUID, error: IrrRunError) -> None:
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


def _metrics_payload(analysis: _IrrAnalysis) -> dict[str, Any]:
    eve = analysis.eve
    return {
        "eve_base_ghs": str(eve.base_eve),
        "worst_scenario": eve.worst_scenario_code,
        "worst_eve_change_ghs": str(eve.worst_delta_eve),
        "worst_eve_change_pct_tier1": str(eve.worst_delta_eve_pct_tier1),
        "eve_limit_pct": str(analysis.eve_limit_pct),
        "ear_up_200_ghs": str(analysis.ear_up),
        "ear_down_200_ghs": str(analysis.ear_down),
        "nii_base_ghs": str(analysis.nii_base),
        "asset_duration": str(analysis.duration.asset_modified),
        "liability_duration": str(analysis.duration.liability_modified),
        "asset_macaulay": str(analysis.duration.asset_macaulay),
        "liability_macaulay": str(analysis.duration.liability_macaulay),
        "duration_gap": str(analysis.duration.duration_gap),
        "cumulative_12m_gap_ghs": str(analysis.gap.cumulative_12m_gap),
        "tier1_ghs": str(eve.tier1),
        "pv_assets_ghs": str(analysis.duration.pv_assets),
        "pv_liabilities_ghs": str(analysis.duration.pv_liabilities),
        "gap_buckets": [
            {
                "bucket": bucket.bucket,
                "midpoint_years": str(bucket.midpoint_years),
                "rsa_ghs": str(bucket.rsa),
                "rsl_ghs": str(bucket.rsl),
                "gap_ghs": str(bucket.gap),
                "cumulative_gap_ghs": str(bucket.cumulative_gap),
                "within_12m": bucket.within_12m,
            }
            for bucket in analysis.gap.buckets
        ],
        "eve_by_scenario": [
            {
                "scenario_code": scenario.scenario_code,
                "eve_ghs": str(scenario.eve),
                "delta_eve_ghs": str(scenario.delta_eve),
                "delta_eve_pct_tier1": str(scenario.delta_eve_pct_tier1),
                "breach": scenario.breach,
            }
            for scenario in eve.scenarios
        ],
    }


def _validation_rows(analysis: _IrrAnalysis) -> tuple[tuple[str, bool, str, str], ...]:
    eve = analysis.eve
    limit = _pct_text(analysis.eve_limit_pct)
    worst_pct = _pct_text(eve.worst_delta_eve_pct_tier1)
    eve_passed = not eve.breach
    eve_message = (
        f"The worst-case EVE change of {worst_pct}% of Tier 1 under the "
        f"{eve.worst_scenario_code} scenario is "
        + ("within" if eve_passed else "above")
        + f" the {limit}% supervisory limit."
    )

    worst_ear = max(abs(analysis.ear_up), abs(analysis.ear_down))
    nii_limit = _pct_text(analysis.nii_limit_pct)
    if analysis.nii_base > _ZERO:
        ear_ratio = (worst_ear / analysis.nii_base * _HUNDRED).quantize(Decimal("0.0001"))
        ear_passed = ear_ratio <= analysis.nii_limit_pct
        ear_message = (
            f"The parallel ±200 bp earnings-at-risk of {ear_ratio}% of base net interest income "
            f"is " + ("within" if ear_passed else "above") + f" the {nii_limit}% limit."
        )
    else:
        ear_passed = True
        ear_message = (
            "Base net interest income is non-positive; the earnings-at-risk limit is not evaluated."
        )

    duration_message = (
        f"The banking-book duration gap is {analysis.duration.duration_gap} years "
        f"(asset {analysis.duration.asset_modified}y vs liability "
        f"{analysis.duration.liability_modified}y)."
    )
    return (
        ("eve_within_limit", eve_passed, "error", eve_message),
        ("ear_within_limit", ear_passed, "warning", ear_message),
        ("duration_gap_reasonable", True, "info", duration_message),
    )


def _metrics_from_analysis(analysis: _IrrAnalysis) -> IrrMetricsRead:
    eve = analysis.eve
    return IrrMetricsRead(
        eve_base_ghs=eve.base_eve,
        worst_scenario_code=eve.worst_scenario_code,  # type: ignore[arg-type]
        worst_eve_change_ghs=eve.worst_delta_eve,
        worst_eve_change_pct_tier1=eve.worst_delta_eve_pct_tier1,
        eve_status=classify_eve_change(abs(eve.worst_delta_eve_pct_tier1), analysis.eve_limit_pct),
        eve_limit_pct=analysis.eve_limit_pct,
        ear_up_200_ghs=analysis.ear_up,
        ear_down_200_ghs=analysis.ear_down,
        nii_base_ghs=analysis.nii_base,
        asset_duration=analysis.duration.asset_modified,
        liability_duration=analysis.duration.liability_modified,
        duration_gap=analysis.duration.duration_gap,
        cumulative_12m_gap_ghs=analysis.gap.cumulative_12m_gap,
        tier1_ghs=eve.tier1,
    )


def _gap_table_from_analysis(analysis: _IrrAnalysis) -> list[IrrGapBucketRead]:
    return [
        IrrGapBucketRead(
            bucket=bucket.bucket,
            midpoint_years=bucket.midpoint_years,
            rsa_ghs=bucket.rsa,
            rsl_ghs=bucket.rsl,
            gap_ghs=bucket.gap,
            cumulative_gap_ghs=bucket.cumulative_gap,
            within_12m=bucket.within_12m,
        )
        for bucket in analysis.gap.buckets
    ]


def _eve_scenarios_from_analysis(analysis: _IrrAnalysis) -> list[IrrEveScenarioRead]:
    return [
        IrrEveScenarioRead(
            scenario_code=scenario.scenario_code,  # type: ignore[arg-type]
            eve_ghs=scenario.eve,
            delta_eve_ghs=scenario.delta_eve,
            delta_eve_pct_tier1=scenario.delta_eve_pct_tier1,
            breach=scenario.breach,
        )
        for scenario in analysis.eve.scenarios
    ]


def _metrics_from_run(run: RegulatoryRun) -> IrrMetricsRead:
    metrics = run.metrics
    limit = _decimal(metrics, "eve_limit_pct")
    worst_pct = _decimal(metrics, "worst_eve_change_pct_tier1")
    return IrrMetricsRead(
        eve_base_ghs=_decimal(metrics, "eve_base_ghs"),
        worst_scenario_code=metrics["worst_scenario"],  # type: ignore[arg-type]
        worst_eve_change_ghs=_decimal(metrics, "worst_eve_change_ghs"),
        worst_eve_change_pct_tier1=worst_pct,
        eve_status=classify_eve_change(abs(worst_pct), limit),
        eve_limit_pct=limit,
        ear_up_200_ghs=_decimal(metrics, "ear_up_200_ghs"),
        ear_down_200_ghs=_decimal(metrics, "ear_down_200_ghs"),
        nii_base_ghs=_decimal(metrics, "nii_base_ghs"),
        asset_duration=_decimal(metrics, "asset_duration"),
        liability_duration=_decimal(metrics, "liability_duration"),
        duration_gap=_decimal(metrics, "duration_gap"),
        cumulative_12m_gap_ghs=_decimal(metrics, "cumulative_12m_gap_ghs"),
        tier1_ghs=_decimal(metrics, "tier1_ghs"),
    )


def _gap_table_from_run(run: RegulatoryRun) -> list[IrrGapBucketRead]:
    buckets: list[dict[str, Any]] = run.metrics.get("gap_buckets", [])
    return [
        IrrGapBucketRead(
            bucket=bucket["bucket"],
            midpoint_years=Decimal(str(bucket["midpoint_years"])),
            rsa_ghs=Decimal(str(bucket["rsa_ghs"])),
            rsl_ghs=Decimal(str(bucket["rsl_ghs"])),
            gap_ghs=Decimal(str(bucket["gap_ghs"])),
            cumulative_gap_ghs=Decimal(str(bucket["cumulative_gap_ghs"])),
            within_12m=bool(bucket["within_12m"]),
        )
        for bucket in buckets
    ]


def _eve_scenarios_from_run(run: RegulatoryRun) -> list[IrrEveScenarioRead]:
    scenarios: list[dict[str, Any]] = run.metrics.get("eve_by_scenario", [])
    return [
        IrrEveScenarioRead(
            scenario_code=scenario["scenario_code"],
            eve_ghs=Decimal(str(scenario["eve_ghs"])),
            delta_eve_ghs=Decimal(str(scenario["delta_eve_ghs"])),
            delta_eve_pct_tier1=Decimal(str(scenario["delta_eve_pct_tier1"])),
            breach=bool(scenario["breach"]),
        )
        for scenario in scenarios
    ]


def _build_trend(
    db: Session, ctx: TenantContext, bank: Bank, periods: list[BankReportingPeriod]
) -> list[IrrTrendPointRead]:
    points: list[IrrTrendPointRead] = []
    for period in periods:
        run = _latest_succeeded_baseline_run(db, ctx, bank, period.id)
        if run is not None:
            metrics = run.metrics
            points.append(
                IrrTrendPointRead(
                    reporting_period_id=period.id,
                    label=period.label,
                    period_end=period.period_end,
                    worst_eve_change_pct_tier1=_decimal(metrics, "worst_eve_change_pct_tier1"),
                    duration_gap=_decimal(metrics, "duration_gap"),
                    cumulative_12m_gap_ghs=_decimal(metrics, "cumulative_12m_gap_ghs"),
                    stored=True,
                )
            )
            continue
        try:
            analysis = _compute_inline(db, ctx, bank, period)
        except (MissingParameterError, IrrComputationError, IrrRunError, UnsupportedShockError):
            continue
        points.append(
            IrrTrendPointRead(
                reporting_period_id=period.id,
                label=period.label,
                period_end=period.period_end,
                worst_eve_change_pct_tier1=analysis.eve.worst_delta_eve_pct_tier1,
                duration_gap=analysis.duration.duration_gap,
                cumulative_12m_gap_ghs=analysis.gap.cumulative_12m_gap,
                stored=False,
            )
        )
    return points


def _compute_inline(
    db: Session, ctx: TenantContext, bank: Bank, period: BankReportingPeriod
) -> _IrrAnalysis:
    facts = _load_facts(db, ctx, bank, period)
    active = _load_irr_params_or_none(db, ctx, bank, period.period_end)
    return _run_analysis(db, ctx, bank, period, facts, active)


def _compute_inline_or_409(
    db: Session, ctx: TenantContext, bank: Bank, period: BankReportingPeriod
) -> _IrrAnalysis:
    try:
        return _compute_inline(db, ctx, bank, period)
    except MissingParameterError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error_code": "missing_parameter", "message": str(exc), "parameter": exc.name},
        ) from exc
    except IrrRunError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error_code": exc.code, "message": exc.message},
        ) from exc
    except UnsupportedShockError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error_code": "unsupported_shock", "message": str(exc)},
        ) from exc
    except IrrComputationError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error_code": "calculation_error", "message": str(exc)},
        ) from exc


def _positions_from_facts(
    facts: list[BankFinancialFact], curve: dict[Decimal, Decimal]
) -> list[IrrPosition]:
    positions: list[IrrPosition] = []
    for fact in facts:
        if fact.fact_group != "irr_position":
            continue
        attributes = fact.attributes
        positions.append(
            IrrPosition(
                side=attributes["side"],
                bucket=attributes["bucket"],
                amount=Decimal(str(fact.amount)),
                rate_pct=Decimal(str(attributes["rate_pct"])),
                fixed_or_float=attributes["fixed_or_float"],
                midpoint_years=Decimal(str(attributes["midpoint_years"])),
                source=attributes.get("source", "unknown"),
            )
        )
    for fact in facts:
        if fact.fact_group != "irr_swap":
            continue
        positions.extend(_swap_legs(fact, curve))
    return positions


def _swap_legs(fact: BankFinancialFact, curve: dict[Decimal, Decimal]) -> list[IrrPosition]:
    """Decompose an interest-rate swap fact into its two hedge legs.

    The fact attributes locate the leg the bank RECEIVES (``receive_bucket`` /
    ``receive_midpoint_years`` → the asset leg) and the leg it PAYS
    (``pay_bucket`` / ``pay_midpoint_years`` → the liability leg);
    ``pay_rate_pct`` always carries the swap's FIXED rate (the starter-template
    column keeps its pay-fixed name for both directions). ``direction``
    determines which leg is fixed and which floats:

    - ``pay_fixed``: receive leg floats (rate = base-curve zero at its
      midpoint, i.e. the current floating index rate), pay leg is fixed.
    - ``receive_fixed``: the mirror image — receive leg is fixed, pay leg
      floats at the base-curve zero for its midpoint.

    Both legs price into gap/EVE/duration and accrue into base NII, where they
    net to the swap carry (see ``app.domain.irr.engine.compute_nii``). An
    unrecognized direction fails the run as data rather than mispricing.
    """
    attributes = fact.attributes
    direction = str(attributes.get("direction", "pay_fixed")).strip().lower()
    if direction not in ("pay_fixed", "receive_fixed"):
        raise IrrRunError(
            "unsupported_swap_direction",
            f"Swap fact '{fact.category}' has unsupported direction {direction!r}; "
            "expected 'pay_fixed' or 'receive_fixed'.",
            {"category": fact.category, "direction": direction},
        )
    notional = Decimal(str(fact.amount))
    receive_midpoint = Decimal(str(attributes["receive_midpoint_years"]))
    pay_midpoint = Decimal(str(attributes["pay_midpoint_years"]))
    fixed_rate = Decimal(str(attributes["pay_rate_pct"]))
    if direction == "pay_fixed":
        receive_rate = curve.get(receive_midpoint, _ZERO)
        pay_rate = fixed_rate
        receive_kind, pay_kind = "float", "fixed"
    else:
        receive_rate = fixed_rate
        pay_rate = curve.get(pay_midpoint, _ZERO)
        receive_kind, pay_kind = "fixed", "float"
    return [
        IrrPosition(
            side="asset",
            bucket=attributes["receive_bucket"],
            amount=notional,
            rate_pct=receive_rate,
            fixed_or_float=receive_kind,
            midpoint_years=receive_midpoint,
            source="swap",
            is_hedge=True,
        ),
        IrrPosition(
            side="liability",
            bucket=attributes["pay_bucket"],
            amount=notional,
            rate_pct=pay_rate,
            fixed_or_float=pay_kind,
            midpoint_years=pay_midpoint,
            source="swap",
            is_hedge=True,
        ),
    ]


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
                BankFinancialFact.fact_group.in_(_IRR_FACT_GROUPS),
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


def _load_irr_params_or_none(
    db: Session, ctx: TenantContext, bank: Bank, as_of: date
) -> _IrrParams | None:
    shock_rows = [
        row
        for row in get_active_params(
            db, ctx.organization_id, bank.jurisdiction_code, ParamStressShock, as_of
        )
        if row.module == MODULE_IRR
    ]
    curve: dict[Decimal, Decimal] = {}
    scenario_shocks: dict[str, dict[str, Decimal]] = {}
    for row in shock_rows:
        if row.scenario_code == BASE_CURVE_SCENARIO:
            midpoint = Decimal(row.shock_key.removesuffix("y"))
            curve[midpoint] = Decimal(str(row.shock_value))
        else:
            scenario_shocks.setdefault(row.scenario_code, {})[row.shock_key] = Decimal(
                str(row.shock_value)
            )
    thresholds = {
        row.threshold_code: Decimal(str(row.value_pct))
        for row in get_active_params(
            db, ctx.organization_id, bank.jurisdiction_code, ParamCapitalThreshold, as_of
        )
    }
    if (
        not curve
        or not scenario_shocks
        or EVE_LIMIT_THRESHOLD not in thresholds
        or NII_LIMIT_THRESHOLD not in thresholds
    ):
        return None
    return _IrrParams(
        curve=curve,
        scenario_shocks=scenario_shocks,
        eve_limit_pct=thresholds[EVE_LIMIT_THRESHOLD],
        nii_limit_pct=thresholds[NII_LIMIT_THRESHOLD],
    )


def _build_snapshot(
    bank: Bank,
    period: BankReportingPeriod,
    scenario_code: str,
    facts: list[BankFinancialFact],
    active: _IrrParams | None,
) -> dict[str, Any]:
    return {
        "schema_version": INPUT_SCHEMA_VERSION,
        "module": MODULE_IRR,
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
        "facts": [
            {
                "id": str(fact.id),
                "fact_group": fact.fact_group,
                "category": fact.category,
                "amount": str(fact.amount),
                "attributes": _sorted_attributes(fact.attributes),
            }
            for fact in facts
        ],
        "parameters": _snapshot_parameters(active),
    }


def _snapshot_parameters(active: _IrrParams | None) -> dict[str, Any]:
    if active is None:
        return {"base_curve_pct": {}, "scenario_shocks": {}, "limits_pct": {}}
    return {
        "base_curve_pct": {
            str(midpoint): str(rate) for midpoint, rate in sorted(active.curve.items())
        },
        "scenario_shocks": {
            scenario: {key: str(value) for key, value in sorted(shocks.items())}
            for scenario, shocks in sorted(active.scenario_shocks.items())
        },
        "limits_pct": {
            EVE_LIMIT_THRESHOLD: str(active.eve_limit_pct),
            NII_LIMIT_THRESHOLD: str(active.nii_limit_pct),
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
            RegulatoryRun.module == MODULE_IRR,
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
