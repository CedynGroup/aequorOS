"""Regulatory capital runs: Basel RWA/ratio engine orchestration, dashboard, BSD-2 preview.

Follows the immutable calculation-run lifecycle established by
``app.services.regulatory_liquidity``: runs commit ``queued`` and ``running``
before executing, persist the full canonical input snapshot with a SHA-256
``input_hash``, and record failures as data (named error codes) rather than
HTTP 500s. The arithmetic itself lives in the pure engine at
``app.domain.capital.engine``.
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
from app.domain.capital.engine import (
    TRIGGER_EARLY_WARNING,
    CapitalComputationError,
    CapitalFact,
    CapitalLineItem,
    CapitalParams,
    CapitalRatiosResult,
    CapitalStressResult,
    MissingParameterError,
    RwaResult,
    UnsupportedShockError,
    classify_capital_ratio,
    compute_capital_ratios,
    compute_rwa,
    money,
    ratio_pct,
    run_capital_stress,
)
from app.models import (
    Bank,
    BankFinancialFact,
    BankReportingPeriod,
    ParamCapitalThreshold,
    ParamRiskWeight,
    ParamStressShock,
    RegulatoryLineItem,
    RegulatoryMetricResult,
    RegulatoryRun,
    RegulatoryValidation,
)
from app.schemas.banks import BankRead, BankReportingPeriodRead
from app.schemas.regulatory_capital import (
    Bsd2HeaderRead,
    Bsd2PreviewRead,
    Bsd2RatioRowRead,
    Bsd2RowRead,
    Bsd2SummaryRowRead,
    Bsd2WeightedRowRead,
    CapitalBuffersRead,
    CapitalDashboardRead,
    CapitalLineRead,
    CapitalMetricsRead,
    CapitalScenarioBatchCreate,
    CapitalStructureRead,
    CapitalStructureSummaryRead,
    CapitalTrendPointRead,
    CapitalValidationRead,
    RwaBreakdownRead,
    RwaCompositionRead,
)
from app.schemas.regulatory_liquidity import (
    RegulatoryRunBatchRead,
    RegulatoryRunCreate,
    RegulatoryRunRead,
)
from app.services.audit import record_event
from app.services.jurisdictions import regulator_name
from app.services.live_block import live_block
from app.services.live_types import (
    LiveModuleResult,
    findings_from_validations,
    worst_status,
)
from app.services.params import get_active_params
from app.services.regulatory_liquidity import get_regulatory_run, preview_note

ENGINE_VERSION = "regulatory-capital-v1.0.0"
INPUT_SCHEMA_VERSION = "bank-facts-v2"
OUTPUT_SCHEMA_VERSION = "capital-metrics-v1"
MODULE_CAPITAL = "capital"
BASELINE_SCENARIO = "baseline"
CAPITAL_SCENARIO_CODES = ("baseline", "mild", "moderate", "severe")

BSD2_FORM_CODE = "BSD-2"
BSD2_FORM_TITLE = "Capital Adequacy Return"
CAR_EARLY_WARNING_LABEL = "Early warning / conservation buffer floor"

_ZERO = Decimal("0")
_HUNDRED = Decimal("100")
_REQUIRED_THRESHOLDS = (
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
# Only these fact groups participate in the capital module; keeping the snapshot
# scoped to them makes the input hash insensitive to unrelated (LCR inflow) edits.
_CAPITAL_FACT_GROUPS = (
    "balance_sheet",
    "capital_component",
    "loan_exposure",
    "market_risk",
    "off_balance",
    "operational_income",
    "securities",
)
_CET1_LINE_PREFIX = "cet1:"
_AT1_LINE_PREFIX = "at1:"
_T2_LINE_PREFIX = "t2:"
_GP_LINE_CODE = "t2:general_provisions"


class CapitalRunError(Exception):
    """Domain input failure persisted onto the run instead of raising HTTP 500."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


@dataclass(frozen=True)
class _ActiveCapitalParams:
    risk_weights: dict[str, Decimal]
    thresholds: dict[str, Decimal]


def create_capital_run(
    db: Session, ctx: TenantContext, bank_id: UUID, payload: RegulatoryRunCreate
) -> RegulatoryRunRead:
    _require_actor(ctx)
    bank = _get_bank_or_404(db, ctx, bank_id)
    period = _get_period_or_404(db, ctx, bank, payload.reporting_period_id)
    return _create_and_execute(db, ctx, bank, period, payload.scenario_code)


def run_all_capital_scenarios(
    db: Session, ctx: TenantContext, bank_id: UUID, payload: CapitalScenarioBatchCreate
) -> RegulatoryRunBatchRead:
    _require_actor(ctx)
    bank = _get_bank_or_404(db, ctx, bank_id)
    period = _get_period_or_404(db, ctx, bank, payload.reporting_period_id)
    runs = [
        _create_and_execute(db, ctx, bank, period, scenario_code)
        for scenario_code in CAPITAL_SCENARIO_CODES
    ]
    return RegulatoryRunBatchRead(bank_id=bank.id, reporting_period_id=period.id, runs=runs)


def get_capital_dashboard(
    db: Session, ctx: TenantContext, bank_id: UUID, reporting_period_id: UUID | None = None
) -> CapitalDashboardRead:
    bank = _get_bank_or_404(db, ctx, bank_id)
    periods = _list_periods_ascending(db, ctx, bank)
    if not periods:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Reporting period not found."
        )
    if reporting_period_id is None:
        period = periods[-1]
    else:
        period = _get_period_or_404(db, ctx, bank, reporting_period_id)

    latest_run = _latest_succeeded_baseline_run(db, ctx, bank, period.id)
    if latest_run is not None:
        metrics = _metrics_from_run(db, latest_run)
        sections = _sections_from_run(db, latest_run)
        validations = [
            CapitalValidationRead(
                rule_code=item.rule_code,
                passed=item.passed,
                severity=item.severity,  # type: ignore[arg-type]
                message=item.message,
            )
            for item in _stored_validations(db, latest_run)
        ]
        stored = True
    else:
        rwa, ratios, engine_params = _compute_inline_or_409(db, ctx, bank, period)
        metrics = _metrics_from_results(rwa, ratios)
        sections = _sections_from_engine(rwa, ratios)
        validations = [
            CapitalValidationRead(
                rule_code=rule_code,
                passed=passed,
                severity=severity,  # type: ignore[arg-type]
                message=message,
            )
            for rule_code, passed, severity, message in _validation_rows(
                ratios, engine_params, None
            )
        ]
        stored = False

    active = _load_active_params(db, ctx, bank, period.period_end)
    return CapitalDashboardRead(
        bank=BankRead.model_validate(bank, from_attributes=True),
        period=BankReportingPeriodRead.model_validate(period, from_attributes=True),
        stored=stored,
        latest_run_id=latest_run.id if latest_run is not None else None,
        metrics=metrics,
        rwa_composition=RwaCompositionRead(
            credit_rwa_ghs=metrics.credit_rwa_ghs,
            market_rwa_ghs=metrics.market_rwa_ghs,
            operational_rwa_ghs=metrics.operational_rwa_ghs,
            total_rwa_ghs=metrics.total_rwa_ghs,
            credit_lines=sections.get("credit_rwa", []),
        ),
        capital_structure=_structure_from_lines(sections.get("capital_component", [])),
        trend=_build_trend(db, ctx, bank, periods),
        buffers=_buffers_or_409(active.thresholds, metrics.car_pct),
        validations=validations,
        live=live_block(db, ctx, bank.id, period.id, MODULE_CAPITAL),
    )


def get_capital_structure(
    db: Session, ctx: TenantContext, bank_id: UUID, reporting_period_id: UUID | None = None
) -> CapitalStructureRead:
    bank, period, run = _baseline_run_or_409(
        db, ctx, bank_id, reporting_period_id, artifact="the capital structure"
    )
    sections = _sections_from_run(db, run)
    summary = _structure_from_lines(sections.get("capital_component", []))
    return CapitalStructureRead(
        bank_id=bank.id,
        reporting_period_id=period.id,
        run_id=run.id,
        **summary.model_dump(),
    )


def get_rwa_breakdown(
    db: Session, ctx: TenantContext, bank_id: UUID, reporting_period_id: UUID | None = None
) -> RwaBreakdownRead:
    bank, period, run = _baseline_run_or_409(
        db, ctx, bank_id, reporting_period_id, artifact="the RWA breakdown"
    )
    sections = _sections_from_run(db, run)
    metrics = _decimal_metrics(run)
    return RwaBreakdownRead(
        bank_id=bank.id,
        reporting_period_id=period.id,
        run_id=run.id,
        credit_rwa_ghs=metrics["credit_rwa_ghs"],
        market_rwa_ghs=metrics["market_rwa_ghs"],
        operational_rwa_ghs=metrics["operational_rwa_ghs"],
        total_rwa_ghs=metrics["total_rwa_ghs"],
        credit_lines=sections.get("credit_rwa", []),
        market_lines=sections.get("market_rwa", []),
        operational_lines=sections.get("operational_rwa", []),
    )


def get_bsd2_preview(
    db: Session, ctx: TenantContext, bank_id: UUID, reporting_period_id: UUID
) -> Bsd2PreviewRead:
    bank, period, run = _baseline_run_or_409(
        db, ctx, bank_id, reporting_period_id, artifact="the BSD-2 preview"
    )
    sections = _sections_from_run(db, run)
    metrics = _decimal_metrics(run)
    thresholds = _load_active_params(db, ctx, bank, period.period_end).thresholds
    cap_pct = thresholds.get("tier2_gp_cap_pct_credit_rwa")
    if cap_pct is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "missing_parameter",
                "message": (
                    "The tier2_gp_cap_pct_credit_rwa threshold parameter is not configured."
                ),
            },
        )

    cet1_components, cet1_deductions, at1_components, tier2_components = _partition_components(
        sections.get("capital_component", [])
    )
    cet1_total = _weighted_total(cet1_components) + _weighted_total(cet1_deductions)
    at1_total = _weighted_total(at1_components)
    tier2_total = _weighted_total(tier2_components)
    tier1_total = cet1_total + at1_total

    tier2_rows = [
        Bsd2RowRead(
            row_code=f"6.{index}",
            description=_tier2_row_description(line, cap_pct, metrics["credit_rwa_ghs"]),
            amount=line.weighted_amount,
        )
        for index, line in enumerate(tier2_components, start=1)
    ]
    ratio_rows = _ratio_rows(db, run)
    validations = [
        CapitalValidationRead(
            rule_code=item.rule_code,
            passed=item.passed,
            severity=item.severity,  # type: ignore[arg-type]
            message=item.message,
        )
        for item in _stored_validations(db, run)
    ]
    regulator = regulator_name(db, bank)
    return Bsd2PreviewRead(
        header=Bsd2HeaderRead(
            form_code=BSD2_FORM_CODE,
            form_title=BSD2_FORM_TITLE,
            regulator=regulator,
            bank_name=bank.name,
            license_type=bank.license_type,
            reporting_period_label=period.label,
            period_end=period.period_end,
            currency=bank.currency,
            generated_at=datetime.now(UTC),
            preview_note=preview_note(regulator),
        ),
        run_id=run.id,
        scenario_code=run.scenario_code,  # type: ignore[arg-type]
        cet1_rows=_amount_rows(cet1_components, prefix="1"),
        deduction_rows=_amount_rows(cet1_deductions, prefix="2"),
        cet1_total=Bsd2SummaryRowRead(
            row_code="3.0",
            description="Common Equity Tier 1 Capital (CET1)",
            value=cet1_total,
            unit="ghs",
        ),
        at1_rows=_amount_rows(at1_components, prefix="4"),
        tier1_total=Bsd2SummaryRowRead(
            row_code="5.0", description="Tier 1 Capital", value=tier1_total, unit="ghs"
        ),
        tier2_rows=tier2_rows,
        total_capital=Bsd2SummaryRowRead(
            row_code="7.0",
            description="Total Regulatory Capital",
            value=tier1_total + tier2_total,
            unit="ghs",
        ),
        credit_rwa_rows=_weighted_rows(sections.get("credit_rwa", []), prefix="8"),
        market_rwa_rows=_weighted_rows(sections.get("market_rwa", []), prefix="9"),
        operational_rwa_rows=_weighted_rows(sections.get("operational_rwa", []), prefix="10"),
        total_rwa=Bsd2SummaryRowRead(
            row_code="11.0",
            description="Total Risk-Weighted Assets",
            value=metrics["total_rwa_ghs"],
            unit="ghs",
        ),
        ratio_rows=ratio_rows,
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
    active = _load_active_params(db, ctx, bank, period.period_end)
    shocks = (
        _load_shocks(db, ctx, bank, scenario_code, period.period_end)
        if scenario_code != BASELINE_SCENARIO
        else {}
    )
    snapshot = _build_snapshot(bank, period, scenario_code, facts, active, shocks)

    run = RegulatoryRun(
        organization_id=ctx.organization_id,
        bank_id=bank.id,
        reporting_period_id=period.id,
        module=MODULE_CAPITAL,
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
            "module": MODULE_CAPITAL,
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
        if not facts:
            raise CapitalRunError(
                "financial_facts_missing",
                "The reporting period has no financial facts to analyze.",
                {"reporting_period_id": str(period.id)},
            )
        engine_facts = tuple(_to_engine_fact(fact) for fact in facts)
        engine_params = _engine_params(active)
        if scenario_code == BASELINE_SCENARIO:
            rwa = compute_rwa(engine_facts, engine_params)
            ratios = compute_capital_ratios(engine_facts, rwa, engine_params)
            _persist_success(db, ctx, run, rwa, ratios, engine_params, None)
        else:
            if not shocks:
                raise CapitalRunError(
                    "missing_parameter",
                    f"No capital stress shocks are configured for scenario '{scenario_code}'.",
                    {"scenario_code": scenario_code},
                )
            stress = run_capital_stress(scenario_code, engine_facts, engine_params, shocks)
            _persist_success(db, ctx, run, stress.rwa, stress.ratios, engine_params, stress)
    except CapitalRunError as exc:
        _persist_failure(db, ctx, run_id, exc)
    except MissingParameterError as exc:
        _persist_failure(
            db,
            ctx,
            run_id,
            CapitalRunError(
                "missing_parameter",
                f"No active capital parameter covers '{exc.name}'.",
                {"parameter": exc.name},
            ),
        )
    except UnsupportedShockError as exc:
        _persist_failure(
            db,
            ctx,
            run_id,
            CapitalRunError(
                "unsupported_shock",
                str(exc),
                {"scenario_code": exc.scenario_code, "shock_key": exc.shock_key},
            ),
        )
    except CapitalComputationError as exc:
        _persist_failure(
            db,
            ctx,
            run_id,
            CapitalRunError("calculation_error", str(exc), None),
        )
    except HTTPException:
        raise
    except Exception:
        _persist_failure(
            db,
            ctx,
            run_id,
            CapitalRunError(
                "calculation_error",
                "The capital metrics could not be calculated.",
                {
                    "corrective_action": (
                        "Review the run inputs and retry. Contact support if it fails again."
                    )
                },
            ),
        )
    db.expire_all()
    return get_regulatory_run(db, ctx, bank.id, run_id)


def _persist_success(  # noqa: PLR0913
    db: Session,
    ctx: TenantContext,
    run: RegulatoryRun,
    rwa: RwaResult,
    ratios: CapitalRatiosResult,
    params: CapitalParams,
    stress: CapitalStressResult | None,
) -> None:
    metrics: dict[str, Any] = {
        "car_pct": str(ratios.car_pct),
        "tier1_ratio_pct": str(ratios.tier1_ratio_pct),
        "cet1_ratio_pct": str(ratios.cet1_ratio_pct),
        "leverage_ratio_pct": str(ratios.leverage_ratio_pct),
        "total_rwa_ghs": str(rwa.total_rwa),
        "credit_rwa_ghs": str(rwa.credit_rwa),
        "market_rwa_ghs": str(rwa.market_rwa),
        "operational_rwa_ghs": str(rwa.operational_rwa),
        "total_capital_ghs": str(ratios.total_capital),
    }
    if stress is not None:
        metrics["stress_path"] = [
            {
                "quarter": row.quarter,
                "cet1_capital": str(row.cet1_capital),
                "tier1_capital": str(row.tier1_capital),
                "total_capital": str(row.total_capital),
                "credit_rwa": str(row.credit_rwa),
                "market_rwa": str(row.market_rwa),
                "operational_rwa": str(row.operational_rwa),
                "total_rwa": str(row.total_rwa),
                "cet1_ratio": str(row.cet1_ratio),
                "tier1_ratio": str(row.tier1_ratio),
                "car": str(row.car),
                "leverage_ratio": str(row.leverage_ratio),
            }
            for row in stress.path
        ]
        metrics["triggers"] = [
            {
                "code": trigger.code,
                "threshold_pct": str(trigger.threshold_pct),
                "fired": trigger.fired,
                "first_quarter": trigger.first_quarter,
                "action": trigger.action,
            }
            for trigger in stress.triggers
        ]
    run.metrics = metrics

    metric_rows: list[tuple[str, Decimal, str, Decimal | None, str]] = [
        ("car_pct", ratios.car_pct, "pct", params.car_min_pct, ratios.car_status),
        (
            "tier1_ratio_pct",
            ratios.tier1_ratio_pct,
            "pct",
            params.tier1_min_pct,
            ratios.tier1_status,
        ),
        ("cet1_ratio_pct", ratios.cet1_ratio_pct, "pct", params.cet1_min_pct, ratios.cet1_status),
        (
            "leverage_ratio_pct",
            ratios.leverage_ratio_pct,
            "pct",
            params.leverage_min_pct,
            ratios.leverage_status,
        ),
    ]
    if stress is not None:
        end_state = stress.path[-1]
        metric_rows.append(
            (
                "car_pct_end",
                end_state.car,
                "pct",
                params.car_min_pct,
                classify_capital_ratio(end_state.car, params.car_min_pct),
            )
        )
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
    for position, item in enumerate((*rwa.line_items, *ratios.line_items), start=1):
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
        _validation_rows(ratios, params, stress), start=1
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
            "car_pct": str(ratios.car_pct),
            "total_rwa_ghs": str(rwa.total_rwa),
        },
    )
    db.commit()


def _persist_failure(db: Session, ctx: TenantContext, run_id: UUID, error: CapitalRunError) -> None:
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


def _validation_rows(
    ratios: CapitalRatiosResult, params: CapitalParams, stress: CapitalStressResult | None
) -> tuple[tuple[str, bool, str, str], ...]:
    ratio_checks = (
        ("car_above_minimum", "CAR", ratios.car_pct, params.car_min_pct),
        ("cet1_above_minimum", "CET1 ratio", ratios.cet1_ratio_pct, params.cet1_min_pct),
        ("tier1_above_minimum", "Tier 1 ratio", ratios.tier1_ratio_pct, params.tier1_min_pct),
        (
            "leverage_above_minimum",
            "leverage ratio",
            ratios.leverage_ratio_pct,
            params.leverage_min_pct,
        ),
    )
    rows: list[tuple[str, bool, str, str]] = []
    for rule_code, label, value, minimum in ratio_checks:
        passed = value >= minimum
        rows.append(
            (
                rule_code,
                passed,
                "error",
                f"The {label} of {_pct_text(value)}% is "
                + ("at or above" if passed else "below")
                + f" the {_pct_text(minimum)}% regulatory minimum.",
            )
        )
    cap_pct = _pct_text(params.tier2_gp_cap_pct_credit_rwa)
    if ratios.gp_cap_applied:
        gp_message = (
            f"The Tier 2 general provisions cap of {cap_pct}% of credit RWA bound: provisions "
            f"of {ratios.general_provisions_amount} GHS were capped at "
            f"{ratios.general_provisions_cap} GHS."
        )
    else:
        gp_message = (
            f"The Tier 2 general provisions cap of {cap_pct}% of credit RWA did not bind: "
            f"provisions of {ratios.general_provisions_amount} GHS are within the cap of "
            f"{ratios.general_provisions_cap} GHS."
        )
    rows.append(("tier2_gp_cap_applied", True, "info", gp_message))
    if stress is not None:
        for trigger in stress.triggers:
            severity = "warning" if trigger.code == TRIGGER_EARLY_WARNING else "error"
            label = trigger.code.replace("_", " ")
            if trigger.fired:
                message = (
                    f"The {label} trigger at {_pct_text(trigger.threshold_pct)}% CAR fired in "
                    f"quarter {trigger.first_quarter}. {trigger.action}"
                )
            else:
                message = (
                    f"The {label} trigger at {_pct_text(trigger.threshold_pct)}% CAR did not "
                    "fire across the four-quarter stress horizon."
                )
            rows.append((f"capital_trigger_{trigger.code}", not trigger.fired, severity, message))
    return tuple(rows)


def _metrics_from_results(rwa: RwaResult, ratios: CapitalRatiosResult) -> CapitalMetricsRead:
    return CapitalMetricsRead(
        car_pct=ratios.car_pct,
        car_status=ratios.car_status,
        tier1_ratio_pct=ratios.tier1_ratio_pct,
        tier1_status=ratios.tier1_status,
        cet1_ratio_pct=ratios.cet1_ratio_pct,
        cet1_status=ratios.cet1_status,
        leverage_ratio_pct=ratios.leverage_ratio_pct,
        leverage_status=ratios.leverage_status,
        total_rwa_ghs=rwa.total_rwa,
        credit_rwa_ghs=rwa.credit_rwa,
        market_rwa_ghs=rwa.market_rwa,
        operational_rwa_ghs=rwa.operational_rwa,
        total_capital_ghs=ratios.total_capital,
    )


def _metrics_from_run(db: Session, run: RegulatoryRun) -> CapitalMetricsRead:
    statuses = {
        row.metric_code: row.status
        for row in db.scalars(
            select(RegulatoryMetricResult).where(
                RegulatoryMetricResult.run_id == run.id,
                RegulatoryMetricResult.organization_id == run.organization_id,
                RegulatoryMetricResult.bank_id == run.bank_id,
            )
        )
    }
    metrics = _decimal_metrics(run)
    return CapitalMetricsRead(
        car_pct=metrics["car_pct"],
        car_status=statuses.get("car_pct", "red"),  # type: ignore[arg-type]
        tier1_ratio_pct=metrics["tier1_ratio_pct"],
        tier1_status=statuses.get("tier1_ratio_pct", "red"),  # type: ignore[arg-type]
        cet1_ratio_pct=metrics["cet1_ratio_pct"],
        cet1_status=statuses.get("cet1_ratio_pct", "red"),  # type: ignore[arg-type]
        leverage_ratio_pct=metrics["leverage_ratio_pct"],
        leverage_status=statuses.get("leverage_ratio_pct", "red"),  # type: ignore[arg-type]
        total_rwa_ghs=metrics["total_rwa_ghs"],
        credit_rwa_ghs=metrics["credit_rwa_ghs"],
        market_rwa_ghs=metrics["market_rwa_ghs"],
        operational_rwa_ghs=metrics["operational_rwa_ghs"],
        total_capital_ghs=metrics["total_capital_ghs"],
    )


def _decimal_metrics(run: RegulatoryRun) -> dict[str, Decimal]:
    return {
        key: Decimal(str(value))
        for key, value in run.metrics.items()
        if isinstance(value, str | int)
    }


def _sections_from_run(db: Session, run: RegulatoryRun) -> dict[str, list[CapitalLineRead]]:
    items = db.scalars(
        select(RegulatoryLineItem)
        .where(
            RegulatoryLineItem.run_id == run.id,
            RegulatoryLineItem.organization_id == run.organization_id,
            RegulatoryLineItem.bank_id == run.bank_id,
        )
        .order_by(RegulatoryLineItem.position)
    )
    sections: dict[str, list[CapitalLineRead]] = {}
    for item in items:
        sections.setdefault(item.section, []).append(
            CapitalLineRead(
                line_code=item.line_code,
                description=item.description,
                exposure_amount=item.exposure_amount,
                rate_pct=item.rate_pct,
                weighted_amount=item.weighted_amount,
            )
        )
    return sections


def _sections_from_engine(
    rwa: RwaResult, ratios: CapitalRatiosResult
) -> dict[str, list[CapitalLineRead]]:
    sections: dict[str, list[CapitalLineRead]] = {}
    for item in (*rwa.line_items, *ratios.line_items):
        sections.setdefault(item.section, []).append(_line_read_from_engine(item))
    return sections


def _line_read_from_engine(item: CapitalLineItem) -> CapitalLineRead:
    return CapitalLineRead(
        line_code=item.line_code,
        description=item.description,
        exposure_amount=item.exposure_amount,
        rate_pct=item.rate_pct,
        weighted_amount=item.weighted_amount,
    )


def _partition_components(
    lines: list[CapitalLineRead],
) -> tuple[
    list[CapitalLineRead], list[CapitalLineRead], list[CapitalLineRead], list[CapitalLineRead]
]:
    cet1_components = [
        line
        for line in lines
        if line.line_code.startswith(_CET1_LINE_PREFIX) and line.weighted_amount >= _ZERO
    ]
    cet1_deductions = [
        line
        for line in lines
        if line.line_code.startswith(_CET1_LINE_PREFIX) and line.weighted_amount < _ZERO
    ]
    at1_components = [line for line in lines if line.line_code.startswith(_AT1_LINE_PREFIX)]
    tier2_components = [line for line in lines if line.line_code.startswith(_T2_LINE_PREFIX)]
    return cet1_components, cet1_deductions, at1_components, tier2_components


def _weighted_total(lines: list[CapitalLineRead]) -> Decimal:
    return sum((line.weighted_amount for line in lines), _ZERO)


def _structure_from_lines(lines: list[CapitalLineRead]) -> CapitalStructureSummaryRead:
    cet1_components, cet1_deductions, at1_components, tier2_components = _partition_components(
        lines
    )
    cet1_total = _weighted_total(cet1_components) + _weighted_total(cet1_deductions)
    at1_total = _weighted_total(at1_components)
    tier2_total = _weighted_total(tier2_components)
    return CapitalStructureSummaryRead(
        cet1_components=cet1_components,
        cet1_deductions=cet1_deductions,
        at1_components=at1_components,
        tier2_components=tier2_components,
        cet1_capital_ghs=cet1_total,
        at1_capital_ghs=at1_total,
        tier1_capital_ghs=cet1_total + at1_total,
        tier2_capital_ghs=tier2_total,
        total_capital_ghs=cet1_total + at1_total + tier2_total,
    )


def _buffers_or_409(thresholds: dict[str, Decimal], current_car: Decimal) -> CapitalBuffersRead:
    missing = [
        code for code in ("car_min", "car_early_warning", "car_critical") if code not in thresholds
    ]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "missing_parameter",
                "message": (
                    "Required capital threshold parameters are not configured: "
                    + ", ".join(missing)
                    + "."
                ),
            },
        )
    return CapitalBuffersRead(
        car_min_pct=thresholds["car_min"],
        car_early_warning_pct=thresholds["car_early_warning"],
        car_early_warning_label=CAR_EARLY_WARNING_LABEL,
        car_critical_pct=thresholds["car_critical"],
        current_car_pct=current_car,
        headroom_pp=ratio_pct(current_car - thresholds["car_min"]),
    )


# Dashboard trends show a trailing window, not the bank's full period history. With
# 10 years of monthly history (~120 periods) and few stored runs, recomputing every
# period inline on each load cost ~500 queries / ~20s; a trailing year is both fast
# and a readable sparkline. Tune here if a longer horizon is wanted.
_TREND_MAX_POINTS = 13


def _build_trend(
    db: Session, ctx: TenantContext, bank: Bank, periods: list[BankReportingPeriod]
) -> list[CapitalTrendPointRead]:
    points: list[CapitalTrendPointRead] = []
    for period in periods[-_TREND_MAX_POINTS:]:
        run = _latest_succeeded_baseline_run(db, ctx, bank, period.id)
        if run is not None:
            metrics = _decimal_metrics(run)
            points.append(
                CapitalTrendPointRead(
                    reporting_period_id=period.id,
                    label=period.label,
                    period_end=period.period_end,
                    car_pct=metrics["car_pct"],
                    tier1_ratio_pct=metrics["tier1_ratio_pct"],
                    cet1_ratio_pct=metrics["cet1_ratio_pct"],
                    stored=True,
                )
            )
            continue
        try:
            _rwa, ratios, _params = _compute_inline(db, ctx, bank, period)
        except (MissingParameterError, CapitalComputationError, CapitalRunError):
            continue
        points.append(
            CapitalTrendPointRead(
                reporting_period_id=period.id,
                label=period.label,
                period_end=period.period_end,
                car_pct=ratios.car_pct,
                tier1_ratio_pct=ratios.tier1_ratio_pct,
                cet1_ratio_pct=ratios.cet1_ratio_pct,
                stored=False,
            )
        )
    return points


def _compute_inline(
    db: Session, ctx: TenantContext, bank: Bank, period: BankReportingPeriod
) -> tuple[RwaResult, CapitalRatiosResult, CapitalParams]:
    facts = _load_facts(db, ctx, bank, period)
    if not facts:
        raise CapitalRunError(
            "financial_facts_missing",
            "The reporting period has no financial facts to analyze.",
            {"reporting_period_id": str(period.id)},
        )
    active = _load_active_params(db, ctx, bank, period.period_end)
    engine_params = _engine_params(active)
    engine_facts = tuple(_to_engine_fact(fact) for fact in facts)
    rwa = compute_rwa(engine_facts, engine_params)
    ratios = compute_capital_ratios(engine_facts, rwa, engine_params)
    return rwa, ratios, engine_params


def _compute_inline_or_409(
    db: Session, ctx: TenantContext, bank: Bank, period: BankReportingPeriod
) -> tuple[RwaResult, CapitalRatiosResult, CapitalParams]:
    try:
        return _compute_inline(db, ctx, bank, period)
    except MissingParameterError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "missing_parameter",
                "message": str(exc),
                "parameter": exc.name,
            },
        ) from exc
    except CapitalRunError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error_code": exc.code, "message": exc.message},
        ) from exc
    except CapitalComputationError as exc:
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
    active = _load_active_params(db, ctx, bank, period.period_end)
    snapshot = _build_snapshot(bank, period, BASELINE_SCENARIO, facts, active, {})
    return _snapshot_hash(snapshot)


def compute_live(
    db: Session, ctx: TenantContext, bank: Bank, period: BankReportingPeriod
) -> LiveModuleResult:
    """Cheap baseline live view — reuses the dashboard's unstored-branch path
    and creates no RegulatoryRun."""
    rwa, ratios, params = _compute_inline(db, ctx, bank, period)
    metrics = {
        "car_pct": str(ratios.car_pct),
        "tier1_ratio_pct": str(ratios.tier1_ratio_pct),
        "cet1_ratio_pct": str(ratios.cet1_ratio_pct),
        "leverage_ratio_pct": str(ratios.leverage_ratio_pct),
        "total_rwa_ghs": str(rwa.total_rwa),
        "total_capital_ghs": str(ratios.total_capital),
    }
    status = worst_status(
        ratios.car_status, ratios.tier1_status, ratios.cet1_status, ratios.leverage_status
    )
    findings = findings_from_validations(_validation_rows(ratios, params, None), status)
    return LiveModuleResult(
        metrics=metrics,
        status=status,
        input_hash=current_input_hash(db, ctx, bank, period),
        findings=findings,
    )


def _baseline_run_or_409(
    db: Session,
    ctx: TenantContext,
    bank_id: UUID,
    reporting_period_id: UUID | None,
    *,
    artifact: str,
) -> tuple[Bank, BankReportingPeriod, RegulatoryRun]:
    bank = _get_bank_or_404(db, ctx, bank_id)
    if reporting_period_id is None:
        periods = _list_periods_ascending(db, ctx, bank)
        if not periods:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Reporting period not found."
            )
        period = periods[-1]
    else:
        period = _get_period_or_404(db, ctx, bank, reporting_period_id)
    run = _latest_succeeded_baseline_run(db, ctx, bank, period.id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "no_baseline_run",
                "message": (
                    f"A successful baseline capital run is required before {artifact} "
                    "can be generated for this reporting period."
                ),
            },
        )
    return bank, period, run


def _amount_rows(lines: list[CapitalLineRead], *, prefix: str) -> list[Bsd2RowRead]:
    return [
        Bsd2RowRead(
            row_code=f"{prefix}.{index}",
            description=line.description,
            amount=line.weighted_amount,
        )
        for index, line in enumerate(lines, start=1)
    ]


def _weighted_rows(lines: list[CapitalLineRead], *, prefix: str) -> list[Bsd2WeightedRowRead]:
    return [
        Bsd2WeightedRowRead(
            row_code=f"{prefix}.{index}",
            description=line.description,
            balance=line.exposure_amount if line.exposure_amount is not None else _ZERO,
            rate_pct=line.rate_pct if line.rate_pct is not None else _ZERO,
            weighted_amount=line.weighted_amount,
        )
        for index, line in enumerate(lines, start=1)
    ]


def _tier2_row_description(line: CapitalLineRead, cap_pct: Decimal, credit_rwa: Decimal) -> str:
    if line.line_code != _GP_LINE_CODE:
        return line.description
    cap_amount = money(credit_rwa * cap_pct / _HUNDRED)
    exposure = line.exposure_amount if line.exposure_amount is not None else line.weighted_amount
    bound = line.weighted_amount < exposure
    return (
        f"General Provisions (Tier 2 cap {_pct_text(cap_pct)}% of credit RWA = {cap_amount} GHS; "
        + ("cap bound" if bound else "cap not binding")
        + ")"
    )


def _ratio_rows(db: Session, run: RegulatoryRun) -> list[Bsd2RatioRowRead]:
    results = {
        row.metric_code: row
        for row in db.scalars(
            select(RegulatoryMetricResult).where(
                RegulatoryMetricResult.run_id == run.id,
                RegulatoryMetricResult.organization_id == run.organization_id,
                RegulatoryMetricResult.bank_id == run.bank_id,
            )
        )
    }
    layout = (
        ("12.1", "CET1 Ratio", "cet1_ratio_pct"),
        ("12.2", "Tier 1 Ratio", "tier1_ratio_pct"),
        ("12.3", "Capital Adequacy Ratio (CAR)", "car_pct"),
        ("12.4", "Leverage Ratio", "leverage_ratio_pct"),
    )
    rows: list[Bsd2RatioRowRead] = []
    for row_code, description, metric_code in layout:
        result = results.get(metric_code)
        if result is None or result.threshold_min is None:
            continue
        rows.append(
            Bsd2RatioRowRead(
                row_code=row_code,
                description=description,
                value_pct=result.metric_value,
                minimum_pct=result.threshold_min,
                passed=result.metric_value >= result.threshold_min,
            )
        )
    return rows


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
                BankFinancialFact.fact_group.in_(_CAPITAL_FACT_GROUPS),
            )
            .order_by(BankFinancialFact.fact_group, BankFinancialFact.category)
        )
    )


def _to_engine_fact(fact: BankFinancialFact) -> CapitalFact:
    return CapitalFact(
        fact_group=fact.fact_group,
        category=fact.category,
        amount=Decimal(str(fact.amount)),
        risk_weight_code=fact.risk_weight_code,
        ccf_pct=Decimal(str(fact.ccf_pct)) if fact.ccf_pct is not None else None,
        income_year=fact.income_year,
        capital_tier=fact.capital_tier,
        is_deduction=fact.is_deduction,
        side=fact.attributes.get("side"),
    )


def _load_active_params(
    db: Session, ctx: TenantContext, bank: Bank, as_of: date
) -> _ActiveCapitalParams:
    weight_rows = get_active_params(
        db, ctx.organization_id, bank.jurisdiction_code, ParamRiskWeight, as_of
    )
    threshold_rows = get_active_params(
        db, ctx.organization_id, bank.jurisdiction_code, ParamCapitalThreshold, as_of
    )
    return _ActiveCapitalParams(
        risk_weights={row.risk_weight_code: Decimal(str(row.weight_pct)) for row in weight_rows},
        thresholds={row.threshold_code: Decimal(str(row.value_pct)) for row in threshold_rows},
    )


def _engine_params(active: _ActiveCapitalParams) -> CapitalParams:
    missing = [code for code in _REQUIRED_THRESHOLDS if code not in active.thresholds]
    if missing:
        raise CapitalRunError(
            "missing_parameter",
            "Required capital threshold parameters are not configured: " + ", ".join(missing) + ".",
            {"threshold_codes": missing},
        )
    thresholds = active.thresholds
    return CapitalParams(
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


def _load_shocks(
    db: Session, ctx: TenantContext, bank: Bank, scenario_code: str, as_of: date
) -> dict[str, Decimal]:
    rows = get_active_params(
        db, ctx.organization_id, bank.jurisdiction_code, ParamStressShock, as_of
    )
    return {
        row.shock_key: Decimal(str(row.shock_value))
        for row in rows
        if row.module == MODULE_CAPITAL and row.scenario_code == scenario_code
    }


def _build_snapshot(  # noqa: PLR0913
    bank: Bank,
    period: BankReportingPeriod,
    scenario_code: str,
    facts: list[BankFinancialFact],
    active: _ActiveCapitalParams,
    shocks: dict[str, Decimal],
) -> dict[str, Any]:
    return {
        "schema_version": INPUT_SCHEMA_VERSION,
        "module": MODULE_CAPITAL,
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
                    "ccf_pct": str(fact.ccf_pct) if fact.ccf_pct is not None else None,
                    "income_year": fact.income_year,
                    "capital_tier": fact.capital_tier,
                    "is_deduction": fact.is_deduction,
                    "side": fact.attributes.get("side"),
                }
                for fact in facts
            ),
            key=lambda entry: json.dumps(entry, sort_keys=True),
        ),
        "parameters": {
            "risk_weights_pct": _stringified(active.risk_weights),
            "thresholds_pct": _stringified(active.thresholds),
        },
        "shocks": _stringified(shocks),
    }


def _stringified(values: dict[str, Decimal]) -> dict[str, str]:
    return {key: str(value) for key, value in sorted(values.items())}


def _snapshot_hash(snapshot: dict[str, Any]) -> str:
    payload = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def _pct_text(value: Decimal) -> str:
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def _latest_succeeded_baseline_run(
    db: Session, ctx: TenantContext, bank: Bank, reporting_period_id: UUID
) -> RegulatoryRun | None:
    return db.scalar(
        select(RegulatoryRun)
        .where(
            RegulatoryRun.organization_id == ctx.organization_id,
            RegulatoryRun.bank_id == bank.id,
            RegulatoryRun.reporting_period_id == reporting_period_id,
            RegulatoryRun.module == MODULE_CAPITAL,
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
