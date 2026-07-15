"""Regulatory liquidity runs: LCR/NSFR engine orchestration, dashboard, BSD-3 preview.

Follows the immutable calculation-run lifecycle: runs commit ``queued`` and
``running`` before executing, persist the full canonical input snapshot with a
SHA-256 ``input_hash``, and record failures as data (named error codes) rather
than HTTP 500s. The arithmetic itself lives in the pure engine at
``app.domain.liquidity.engine``.
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
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.domain.liquidity.engine import (
    LcrResult,
    LiquidityComputationError,
    LiquidityFact,
    LiquidityParams,
    MissingParameterError,
    NsfrResult,
    UnsupportedShockError,
    apply_liquidity_stress,
    compute_lcr,
    compute_nsfr,
)
from app.models import (
    Bank,
    BankFinancialFact,
    BankReportingPeriod,
    ParamCapitalThreshold,
    ParamLcrRunoffRate,
    ParamNsfrWeight,
    ParamStressShock,
    RegulatoryLineItem,
    RegulatoryMetricResult,
    RegulatoryRun,
    RegulatoryValidation,
)
from app.schemas.banks import BankRead, BankReportingPeriodRead
from app.schemas.regulatory_liquidity import (
    Bsd3HeaderRead,
    Bsd3NsfrSectionRead,
    Bsd3PreviewRead,
    Bsd3RowRead,
    Bsd3SummaryRowRead,
    Bsd3WeightedRowRead,
    LiquidityDashboardLineRead,
    LiquidityDashboardRead,
    LiquidityMetricsRead,
    LiquidityScenarioBatchCreate,
    LiquidityTrendPointRead,
    LiquidityValidationRead,
    RegulatoryLineItemRead,
    RegulatoryMetricResultRead,
    RegulatoryRunBatchRead,
    RegulatoryRunCreate,
    RegulatoryRunErrorRead,
    RegulatoryRunListRead,
    RegulatoryRunRead,
    RegulatoryRunSummaryRead,
    RegulatoryValidationRead,
)
from app.services.audit import record_event
from app.services.params import get_active_params

ENGINE_VERSION = "regulatory-liquidity-v1.0.0"
INPUT_SCHEMA_VERSION = "bank-facts-v1"
OUTPUT_SCHEMA_VERSION = "liquidity-metrics-v1"
MODULE_LIQUIDITY = "liquidity"
BASELINE_SCENARIO = "baseline"
LIQUIDITY_SCENARIO_CODES = ("baseline", "idiosyncratic", "market_wide", "combined")

BSD3_FORM_CODE = "BSD-3"
BSD3_FORM_TITLE = "Liquidity Returns (LCR & NSFR)"
BSD3_REGULATOR = "Bank of Ghana"
BSD3_PREVIEW_NOTE = "PREVIEW ONLY — This system does not file submissions with Bank of Ghana."

_ZERO = Decimal("0")
_REQUIRED_THRESHOLDS = ("lcr_min", "lcr_amber_floor", "nsfr_min", "lcr_inflow_cap_pct")
# Only these fact groups participate in LCR/NSFR; keeping the snapshot scoped to
# them makes the input hash insensitive to unrelated (capital/market) fact edits.
_LIQUIDITY_FACT_GROUPS = (
    "balance_sheet",
    "lcr_inflow",
    "loan_exposure",
    "off_balance",
    "securities",
)


class LiquidityRunError(Exception):
    """Domain input failure persisted onto the run instead of raising HTTP 500."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


@dataclass(frozen=True)
class _ActiveLiquidityParams:
    outflow_rates: dict[str, Decimal]
    inflow_rates: dict[str, Decimal]
    asf_weights: dict[str, Decimal]
    rsf_weights: dict[str, Decimal]
    thresholds: dict[str, Decimal]


def create_liquidity_run(
    db: Session, ctx: TenantContext, bank_id: UUID, payload: RegulatoryRunCreate
) -> RegulatoryRunRead:
    _require_actor(ctx)
    bank = _get_bank_or_404(db, ctx, bank_id)
    period = _get_period_or_404(db, ctx, bank, payload.reporting_period_id)
    return _create_and_execute(db, ctx, bank, period, payload.scenario_code)


def run_all_liquidity_scenarios(
    db: Session, ctx: TenantContext, bank_id: UUID, payload: LiquidityScenarioBatchCreate
) -> RegulatoryRunBatchRead:
    _require_actor(ctx)
    bank = _get_bank_or_404(db, ctx, bank_id)
    period = _get_period_or_404(db, ctx, bank, payload.reporting_period_id)
    runs = [
        _create_and_execute(db, ctx, bank, period, scenario_code)
        for scenario_code in LIQUIDITY_SCENARIO_CODES
    ]
    return RegulatoryRunBatchRead(bank_id=bank.id, reporting_period_id=period.id, runs=runs)


def list_regulatory_runs(  # noqa: PLR0913
    db: Session,
    ctx: TenantContext,
    bank_id: UUID,
    *,
    module: str | None = None,
    reporting_period_id: UUID | None = None,
    scenario_code: str | None = None,
    limit: int = 25,
    offset: int = 0,
) -> RegulatoryRunListRead:
    bank = _get_bank_or_404(db, ctx, bank_id)
    conditions = (
        RegulatoryRun.organization_id == ctx.organization_id,
        RegulatoryRun.bank_id == bank.id,
    )
    if module is not None:
        conditions += (RegulatoryRun.module == module,)
    if reporting_period_id is not None:
        conditions += (RegulatoryRun.reporting_period_id == reporting_period_id,)
    if scenario_code is not None:
        conditions += (RegulatoryRun.scenario_code == scenario_code,)
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
    return RegulatoryRunListRead(
        bank_id=bank.id,
        runs=[_read_summary(run, label) for run, label in rows],
        total=total,
        limit=limit,
        offset=offset,
        has_more=offset + len(rows) < total,
    )


def get_regulatory_run(
    db: Session, ctx: TenantContext, bank_id: UUID, run_id: UUID
) -> RegulatoryRunRead:
    bank = _get_bank_or_404(db, ctx, bank_id)
    return _read_run(db, _run_or_404(db, ctx, bank.id, run_id))


def get_liquidity_dashboard(
    db: Session, ctx: TenantContext, bank_id: UUID, reporting_period_id: UUID | None = None
) -> LiquidityDashboardRead:
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
    sections: dict[str, list[LiquidityDashboardLineRead]]
    if latest_run is not None:
        metrics = _metrics_from_run(db, latest_run)
        sections = _stored_lines_by_section(db, latest_run)
        validations = [
            LiquidityValidationRead(
                rule_code=item.rule_code,
                passed=item.passed,
                severity=item.severity,  # type: ignore[arg-type]
                message=item.message,
            )
            for item in _stored_validations(db, latest_run)
        ]
        stored = True
    else:
        lcr, nsfr, params = _compute_inline_or_409(db, ctx, bank, period)
        metrics = _metrics_from_results(lcr, nsfr)
        sections = {}
        for item in (*lcr.line_items, *nsfr.line_items):
            sections.setdefault(item.section, []).append(
                LiquidityDashboardLineRead(
                    line_code=item.line_code,
                    description=item.description,
                    exposure_amount=item.exposure_amount,
                    rate_pct=item.rate_pct,
                    weighted_amount=item.weighted_amount,
                )
            )
        validations = [
            LiquidityValidationRead(
                rule_code=rule_code,
                passed=passed,
                severity=severity,  # type: ignore[arg-type]
                message=message,
            )
            for rule_code, passed, severity, message in _validation_rows(lcr, nsfr, params)
        ]
        stored = False

    trend = _build_trend(db, ctx, bank, periods)
    return LiquidityDashboardRead(
        bank=BankRead.model_validate(bank, from_attributes=True),
        period=BankReportingPeriodRead.model_validate(period, from_attributes=True),
        stored=stored,
        latest_run_id=latest_run.id if latest_run is not None else None,
        metrics=metrics,
        hqla_composition=sections.get("hqla", []),
        outflows=sections.get("outflow", []),
        inflows=sections.get("inflow", []),
        trend=trend,
        validations=validations,
    )


def get_bsd3_preview(
    db: Session, ctx: TenantContext, bank_id: UUID, reporting_period_id: UUID
) -> Bsd3PreviewRead:
    bank = _get_bank_or_404(db, ctx, bank_id)
    period = _get_period_or_404(db, ctx, bank, reporting_period_id)
    run = _latest_succeeded_baseline_run(db, ctx, bank, period.id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "no_baseline_run",
                "message": (
                    "A successful baseline liquidity run is required before the BSD-3 "
                    "preview can be generated for this reporting period."
                ),
            },
        )

    items = list(
        db.scalars(
            select(RegulatoryLineItem)
            .where(
                RegulatoryLineItem.run_id == run.id,
                RegulatoryLineItem.organization_id == run.organization_id,
                RegulatoryLineItem.bank_id == run.bank_id,
            )
            .order_by(RegulatoryLineItem.position)
        )
    )
    by_section: dict[str, list[RegulatoryLineItem]] = {}
    for item in items:
        by_section.setdefault(item.section, []).append(item)

    metrics = {key: Decimal(str(value)) for key, value in run.metrics.items()}
    hqla_rows = [
        Bsd3RowRead(
            row_code=f"1.{index}",
            description=item.description,
            amount=item.weighted_amount,
        )
        for index, item in enumerate(by_section.get("hqla", []), start=1)
    ]
    outflow_rows = _weighted_rows(by_section.get("outflow", []), prefix="4")
    inflow_rows = _weighted_rows(by_section.get("inflow", []), prefix="6")
    outflows_total = sum((item.weighted_amount for item in by_section.get("outflow", [])), _ZERO)
    net_outflows = metrics["net_outflows_30d_ghs"]
    capped_inflows = outflows_total - net_outflows
    summary_rows = [
        Bsd3SummaryRowRead(
            row_code="3.0",
            description="Total High Quality Liquid Assets",
            value=metrics["hqla_total_ghs"],
            unit="ghs",
        ),
        Bsd3SummaryRowRead(
            row_code="5.0",
            description="Total Cash Outflows (30 days)",
            value=outflows_total,
            unit="ghs",
        ),
        Bsd3SummaryRowRead(
            row_code="7.0",
            description="Total Cash Inflows After Cap (30 days)",
            value=capped_inflows,
            unit="ghs",
        ),
        Bsd3SummaryRowRead(
            row_code="8.0",
            description="Net Cash Outflows (30 days)",
            value=net_outflows,
            unit="ghs",
        ),
        Bsd3SummaryRowRead(
            row_code="9.0",
            description="Liquidity Coverage Ratio",
            value=metrics["lcr_pct"],
            unit="pct",
        ),
    ]
    nsfr_section = Bsd3NsfrSectionRead(
        asf_rows=_weighted_rows(by_section.get("asf", []), prefix="10"),
        asf_total=Bsd3SummaryRowRead(
            row_code="11.0",
            description="Total Available Stable Funding",
            value=metrics["asf_total_ghs"],
            unit="ghs",
        ),
        rsf_rows=_weighted_rows(by_section.get("rsf", []), prefix="12"),
        rsf_total=Bsd3SummaryRowRead(
            row_code="13.0",
            description="Total Required Stable Funding",
            value=metrics["rsf_total_ghs"],
            unit="ghs",
        ),
        nsfr_ratio=Bsd3SummaryRowRead(
            row_code="14.0",
            description="Net Stable Funding Ratio",
            value=metrics["nsfr_pct"],
            unit="pct",
        ),
    )
    validations = [
        LiquidityValidationRead(
            rule_code=item.rule_code,
            passed=item.passed,
            severity=item.severity,  # type: ignore[arg-type]
            message=item.message,
        )
        for item in _stored_validations(db, run)
    ]
    return Bsd3PreviewRead(
        header=Bsd3HeaderRead(
            form_code=BSD3_FORM_CODE,
            form_title=BSD3_FORM_TITLE,
            regulator=BSD3_REGULATOR,
            bank_name=bank.name,
            license_type=bank.license_type,
            reporting_period_label=period.label,
            period_end=period.period_end,
            currency=bank.currency,
            generated_at=datetime.now(UTC),
            preview_note=BSD3_PREVIEW_NOTE,
        ),
        run_id=run.id,
        scenario_code=run.scenario_code,  # type: ignore[arg-type]
        hqla_rows=hqla_rows,
        outflow_rows=outflow_rows,
        inflow_rows=inflow_rows,
        summary_rows=summary_rows,
        nsfr=nsfr_section,
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
        module=MODULE_LIQUIDITY,
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
            "module": MODULE_LIQUIDITY,
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
        engine_facts = tuple(_to_engine_fact(fact) for fact in facts)
        engine_params = _engine_params(active)
        if scenario_code != BASELINE_SCENARIO:
            if not shocks:
                raise LiquidityRunError(
                    "missing_parameter",
                    f"No liquidity stress shocks are configured for scenario '{scenario_code}'.",
                    {"scenario_code": scenario_code},
                )
            engine_facts, engine_params = apply_liquidity_stress(
                scenario_code, engine_facts, engine_params, shocks
            )
        if not engine_facts:
            raise LiquidityRunError(
                "financial_facts_missing",
                "The reporting period has no financial facts to analyze.",
                {"reporting_period_id": str(period.id)},
            )
        lcr = compute_lcr(engine_facts, engine_params)
        nsfr = compute_nsfr(engine_facts, engine_params)
        _persist_success(db, ctx, run, lcr, nsfr, engine_params)
    except LiquidityRunError as exc:
        _persist_failure(db, ctx, run_id, exc)
    except MissingParameterError as exc:
        _persist_failure(
            db,
            ctx,
            run_id,
            LiquidityRunError(
                "missing_parameter",
                f"No active liquidity parameter covers category '{exc.category}'.",
                {"category": exc.category},
            ),
        )
    except UnsupportedShockError as exc:
        _persist_failure(
            db,
            ctx,
            run_id,
            LiquidityRunError(
                "unsupported_shock",
                str(exc),
                {"scenario_code": exc.scenario_code, "shock_key": exc.shock_key},
            ),
        )
    except LiquidityComputationError as exc:
        _persist_failure(
            db,
            ctx,
            run_id,
            LiquidityRunError("calculation_error", str(exc), None),
        )
    except HTTPException:
        raise
    except Exception:
        _persist_failure(
            db,
            ctx,
            run_id,
            LiquidityRunError(
                "calculation_error",
                "The liquidity metrics could not be calculated.",
                {
                    "corrective_action": (
                        "Review the run inputs and retry. Contact support if it fails again."
                    )
                },
            ),
        )
    db.expire_all()
    return _read_run(db, _run_or_404(db, ctx, bank.id, run_id))


def _persist_success(  # noqa: PLR0913
    db: Session,
    ctx: TenantContext,
    run: RegulatoryRun,
    lcr: LcrResult,
    nsfr: NsfrResult,
    params: LiquidityParams,
) -> None:
    run.metrics = {
        "lcr_pct": str(lcr.lcr_pct),
        "nsfr_pct": str(nsfr.nsfr_pct),
        "hqla_total_ghs": str(lcr.hqla_total),
        "net_outflows_30d_ghs": str(lcr.net_outflows_total),
        "asf_total_ghs": str(nsfr.asf_total),
        "rsf_total_ghs": str(nsfr.rsf_total),
    }
    metric_rows: tuple[tuple[str, Decimal, str, Decimal | None, str], ...] = (
        ("lcr_pct", lcr.lcr_pct, "pct", params.lcr_min_pct, lcr.status),
        ("nsfr_pct", nsfr.nsfr_pct, "pct", params.nsfr_min_pct, nsfr.status),
        ("hqla_total_ghs", lcr.hqla_total, "ghs", None, "na"),
        ("net_outflows_30d_ghs", lcr.net_outflows_total, "ghs", None, "na"),
        ("asf_total_ghs", nsfr.asf_total, "ghs", None, "na"),
        ("rsf_total_ghs", nsfr.rsf_total, "ghs", None, "na"),
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
    for position, item in enumerate((*lcr.line_items, *nsfr.line_items), start=1):
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
        _validation_rows(lcr, nsfr, params), start=1
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
            "lcr_pct": str(lcr.lcr_pct),
            "nsfr_pct": str(nsfr.nsfr_pct),
        },
    )
    db.commit()


def _persist_failure(
    db: Session, ctx: TenantContext, run_id: UUID, error: LiquidityRunError
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
            "scenario_code": run.scenario_code,
            "error_code": error.code,
        },
    )
    db.commit()


def _validation_rows(
    lcr: LcrResult, nsfr: NsfrResult, params: LiquidityParams
) -> tuple[tuple[str, bool, str, str], ...]:
    lcr_min = _pct_text(params.lcr_min_pct)
    amber_floor = _pct_text(params.lcr_amber_floor_pct)
    nsfr_min = _pct_text(params.nsfr_min_pct)
    lcr_pct = _pct_text(lcr.lcr_pct)
    nsfr_pct = _pct_text(nsfr.nsfr_pct)

    lcr_above = lcr.lcr_pct >= params.lcr_min_pct
    lcr_amber = params.lcr_amber_floor_pct <= lcr.lcr_pct < params.lcr_min_pct
    nsfr_above = nsfr.nsfr_pct >= params.nsfr_min_pct
    if lcr.inflow_cap_applied:
        cap_message = (
            f"The {_pct_text(params.inflow_cap_pct)}% inflow cap bound: gross inflows of "
            f"{lcr.gross_inflows_total} GHS were capped at {lcr.capped_inflows_total} GHS."
        )
    else:
        cap_message = (
            f"The {_pct_text(params.inflow_cap_pct)}% inflow cap did not bind: gross inflows "
            f"of {lcr.gross_inflows_total} GHS are below the cap of "
            f"{lcr.inflow_cap_amount} GHS."
        )
    return (
        (
            "lcr_above_minimum",
            lcr_above,
            "error",
            f"LCR of {lcr_pct}% is "
            + ("at or above" if lcr_above else "below")
            + f" the {lcr_min}% regulatory minimum.",
        ),
        (
            "lcr_amber_zone",
            not lcr_amber,
            "warning",
            f"LCR of {lcr_pct}% is "
            + ("inside" if lcr_amber else "outside")
            + f" the amber zone between {amber_floor}% and {lcr_min}%.",
        ),
        (
            "nsfr_above_minimum",
            nsfr_above,
            "error",
            f"NSFR of {nsfr_pct}% is "
            + ("at or above" if nsfr_above else "below")
            + f" the {nsfr_min}% regulatory minimum.",
        ),
        ("inflow_cap_applied", True, "info", cap_message),
        (
            "hqla_all_level1",
            lcr.all_hqla_level1,
            "info",
            "All high quality liquid assets are Level 1."
            if lcr.all_hqla_level1
            else "The HQLA stock includes assets below Level 1.",
        ),
    )


def _metrics_from_results(lcr: LcrResult, nsfr: NsfrResult) -> LiquidityMetricsRead:
    return LiquidityMetricsRead(
        lcr_pct=lcr.lcr_pct,
        lcr_status=lcr.status,
        nsfr_pct=nsfr.nsfr_pct,
        nsfr_status=nsfr.status,
        hqla_total_ghs=lcr.hqla_total,
        net_outflows_30d_ghs=lcr.net_outflows_total,
        asf_total_ghs=nsfr.asf_total,
        rsf_total_ghs=nsfr.rsf_total,
    )


def _metrics_from_run(db: Session, run: RegulatoryRun) -> LiquidityMetricsRead:
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
    metrics = {key: Decimal(str(value)) for key, value in run.metrics.items()}
    return LiquidityMetricsRead(
        lcr_pct=metrics["lcr_pct"],
        lcr_status=statuses.get("lcr_pct", "red"),  # type: ignore[arg-type]
        nsfr_pct=metrics["nsfr_pct"],
        nsfr_status=statuses.get("nsfr_pct", "red"),  # type: ignore[arg-type]
        hqla_total_ghs=metrics["hqla_total_ghs"],
        net_outflows_30d_ghs=metrics["net_outflows_30d_ghs"],
        asf_total_ghs=metrics["asf_total_ghs"],
        rsf_total_ghs=metrics["rsf_total_ghs"],
    )


def _stored_lines_by_section(
    db: Session, run: RegulatoryRun
) -> dict[str, list[LiquidityDashboardLineRead]]:
    items = db.scalars(
        select(RegulatoryLineItem)
        .where(
            RegulatoryLineItem.run_id == run.id,
            RegulatoryLineItem.organization_id == run.organization_id,
            RegulatoryLineItem.bank_id == run.bank_id,
        )
        .order_by(RegulatoryLineItem.position)
    )
    sections: dict[str, list[LiquidityDashboardLineRead]] = {}
    for item in items:
        sections.setdefault(item.section, []).append(
            LiquidityDashboardLineRead(
                line_code=item.line_code,
                description=item.description,
                exposure_amount=item.exposure_amount,
                rate_pct=item.rate_pct,
                weighted_amount=item.weighted_amount,
            )
        )
    return sections


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


def _build_trend(
    db: Session, ctx: TenantContext, bank: Bank, periods: list[BankReportingPeriod]
) -> list[LiquidityTrendPointRead]:
    points: list[LiquidityTrendPointRead] = []
    for period in periods:
        run = _latest_succeeded_baseline_run(db, ctx, bank, period.id)
        if run is not None:
            metrics = {key: Decimal(str(value)) for key, value in run.metrics.items()}
            points.append(
                LiquidityTrendPointRead(
                    reporting_period_id=period.id,
                    label=period.label,
                    period_end=period.period_end,
                    lcr_pct=metrics["lcr_pct"],
                    nsfr_pct=metrics["nsfr_pct"],
                    stored=True,
                )
            )
            continue
        try:
            lcr, nsfr, _params = _compute_inline(db, ctx, bank, period)
        except (MissingParameterError, LiquidityComputationError, LiquidityRunError):
            continue
        points.append(
            LiquidityTrendPointRead(
                reporting_period_id=period.id,
                label=period.label,
                period_end=period.period_end,
                lcr_pct=lcr.lcr_pct,
                nsfr_pct=nsfr.nsfr_pct,
                stored=False,
            )
        )
    return points


def _compute_inline(
    db: Session, ctx: TenantContext, bank: Bank, period: BankReportingPeriod
) -> tuple[LcrResult, NsfrResult, LiquidityParams]:
    facts = _load_facts(db, ctx, bank, period)
    if not facts:
        raise LiquidityRunError(
            "financial_facts_missing",
            "The reporting period has no financial facts to analyze.",
            {"reporting_period_id": str(period.id)},
        )
    active = _load_active_params(db, ctx, bank, period.period_end)
    engine_params = _engine_params(active)
    engine_facts = tuple(_to_engine_fact(fact) for fact in facts)
    return (
        compute_lcr(engine_facts, engine_params),
        compute_nsfr(engine_facts, engine_params),
        engine_params,
    )


def _compute_inline_or_409(
    db: Session, ctx: TenantContext, bank: Bank, period: BankReportingPeriod
) -> tuple[LcrResult, NsfrResult, LiquidityParams]:
    try:
        return _compute_inline(db, ctx, bank, period)
    except MissingParameterError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "missing_parameter",
                "message": str(exc),
                "category": exc.category,
            },
        ) from exc
    except LiquidityRunError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error_code": exc.code, "message": exc.message},
        ) from exc
    except LiquidityComputationError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error_code": "calculation_error", "message": str(exc)},
        ) from exc


def _weighted_rows(items: list[RegulatoryLineItem], *, prefix: str) -> list[Bsd3WeightedRowRead]:
    return [
        Bsd3WeightedRowRead(
            row_code=f"{prefix}.{index}",
            description=item.description,
            balance=item.exposure_amount if item.exposure_amount is not None else _ZERO,
            rate_pct=item.rate_pct if item.rate_pct is not None else _ZERO,
            weighted_amount=item.weighted_amount,
        )
        for index, item in enumerate(items, start=1)
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
                BankFinancialFact.fact_group.in_(_LIQUIDITY_FACT_GROUPS),
            )
            .order_by(BankFinancialFact.fact_group, BankFinancialFact.category)
        )
    )


def _to_engine_fact(fact: BankFinancialFact) -> LiquidityFact:
    return LiquidityFact(
        fact_group=fact.fact_group,
        category=fact.category,
        amount=Decimal(str(fact.amount)),
        hqla_level=fact.hqla_level,
        side=fact.attributes.get("side"),
        cash_derived=fact.attributes.get("source") == "cash",
    )


def _load_active_params(
    db: Session, ctx: TenantContext, bank: Bank, as_of: date
) -> _ActiveLiquidityParams:
    runoff_rows = get_active_params(
        db, ctx.organization_id, bank.jurisdiction_code, ParamLcrRunoffRate, as_of
    )
    nsfr_rows = get_active_params(
        db, ctx.organization_id, bank.jurisdiction_code, ParamNsfrWeight, as_of
    )
    threshold_rows = get_active_params(
        db, ctx.organization_id, bank.jurisdiction_code, ParamCapitalThreshold, as_of
    )
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
    thresholds = {row.threshold_code: Decimal(str(row.value_pct)) for row in threshold_rows}
    return _ActiveLiquidityParams(
        outflow_rates=outflow_rates,
        inflow_rates=inflow_rates,
        asf_weights=asf_weights,
        rsf_weights=rsf_weights,
        thresholds=thresholds,
    )


def _engine_params(active: _ActiveLiquidityParams) -> LiquidityParams:
    missing = [code for code in _REQUIRED_THRESHOLDS if code not in active.thresholds]
    if missing:
        raise LiquidityRunError(
            "missing_parameter",
            "Required liquidity threshold parameters are not configured: "
            + ", ".join(missing)
            + ".",
            {"threshold_codes": missing},
        )
    # The BoG MVP parameter set defines one amber floor; it applies to both ratios.
    amber_floor = active.thresholds["lcr_amber_floor"]
    return LiquidityParams(
        outflow_rates=active.outflow_rates,
        inflow_rates=active.inflow_rates,
        asf_weights=active.asf_weights,
        rsf_weights=active.rsf_weights,
        inflow_cap_pct=active.thresholds["lcr_inflow_cap_pct"],
        lcr_min_pct=active.thresholds["lcr_min"],
        lcr_amber_floor_pct=amber_floor,
        nsfr_min_pct=active.thresholds["nsfr_min"],
        nsfr_amber_floor_pct=amber_floor,
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
        if row.module == MODULE_LIQUIDITY and row.scenario_code == scenario_code
    }


def _build_snapshot(  # noqa: PLR0913
    bank: Bank,
    period: BankReportingPeriod,
    scenario_code: str,
    facts: list[BankFinancialFact],
    active: _ActiveLiquidityParams,
    shocks: dict[str, Decimal],
) -> dict[str, Any]:
    return {
        "schema_version": INPUT_SCHEMA_VERSION,
        "module": MODULE_LIQUIDITY,
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
                "hqla_level": fact.hqla_level,
                "side": fact.attributes.get("side"),
                "cash_derived": fact.attributes.get("source") == "cash",
            }
            for fact in facts
        ],
        "parameters": {
            "outflow_runoff_rates_pct": _stringified(active.outflow_rates),
            "inflow_rates_pct": _stringified(active.inflow_rates),
            "asf_weights_pct": _stringified(active.asf_weights),
            "rsf_weights_pct": _stringified(active.rsf_weights),
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


def _error_read(run: RegulatoryRun) -> RegulatoryRunErrorRead | None:
    if not run.error_code or not run.error_message:
        return None
    return RegulatoryRunErrorRead(
        code=run.error_code, message=run.error_message, details=run.error_details
    )


def _read_summary(run: RegulatoryRun, period_label: str) -> RegulatoryRunSummaryRead:
    return RegulatoryRunSummaryRead(
        id=run.id,
        module=run.module,  # type: ignore[arg-type]
        scenario_code=run.scenario_code,  # type: ignore[arg-type]
        status=run.status,  # type: ignore[arg-type]
        reporting_period_id=run.reporting_period_id,
        period_label=period_label,
        engine_version=run.engine_version,
        input_hash=run.input_hash,
        metrics=run.metrics,
        error=_error_read(run),
        created_at=run.created_at,
    )


def _read_run(db: Session, run: RegulatoryRun) -> RegulatoryRunRead:
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
    line_items = list(
        db.scalars(
            select(RegulatoryLineItem)
            .where(
                RegulatoryLineItem.run_id == run.id,
                RegulatoryLineItem.organization_id == run.organization_id,
                RegulatoryLineItem.bank_id == run.bank_id,
            )
            .order_by(RegulatoryLineItem.position)
        )
    )
    validations = _stored_validations(db, run)
    return RegulatoryRunRead(
        id=run.id,
        organization_id=run.organization_id,
        bank_id=run.bank_id,
        reporting_period_id=run.reporting_period_id,
        module=run.module,  # type: ignore[arg-type]
        scenario_code=run.scenario_code,  # type: ignore[arg-type]
        status=run.status,  # type: ignore[arg-type]
        engine_version=run.engine_version,
        input_schema_version=run.input_schema_version,
        output_schema_version=run.output_schema_version,
        input_hash=run.input_hash,
        inputs=run.inputs,
        metrics=run.metrics,
        started_at=run.started_at,
        completed_at=run.completed_at,
        error=_error_read(run),
        metric_results=[RegulatoryMetricResultRead.model_validate(item) for item in metric_results],
        line_items=[RegulatoryLineItemRead.model_validate(item) for item in line_items],
        validations=[RegulatoryValidationRead.model_validate(item) for item in validations],
        created_by=run.created_by,
        created_at=run.created_at,
        updated_at=run.updated_at,
    )


def _run_or_404(db: Session, ctx: TenantContext, bank_id: UUID, run_id: UUID) -> RegulatoryRun:
    run = db.scalar(
        select(RegulatoryRun).where(
            RegulatoryRun.id == run_id,
            RegulatoryRun.organization_id == ctx.organization_id,
            RegulatoryRun.bank_id == bank_id,
        )
    )
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Regulatory run not found."
        )
    return run


def _latest_succeeded_baseline_run(
    db: Session, ctx: TenantContext, bank: Bank, reporting_period_id: UUID
) -> RegulatoryRun | None:
    return db.scalar(
        select(RegulatoryRun)
        .where(
            RegulatoryRun.organization_id == ctx.organization_id,
            RegulatoryRun.bank_id == bank.id,
            RegulatoryRun.reporting_period_id == reporting_period_id,
            RegulatoryRun.module == MODULE_LIQUIDITY,
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
