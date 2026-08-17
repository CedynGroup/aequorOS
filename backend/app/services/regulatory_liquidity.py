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
    SHOCK_FX_DEPRECIATION,
    SHOCK_NMD_RUNOFF_PREFIX,
    CurrencyGapResult,
    LcrResult,
    LiquidityComputationError,
    LiquidityFact,
    LiquidityParams,
    MissingParameterError,
    NsfrResult,
    StressedLadder,
    UnsupportedShockError,
    apply_liquidity_stress,
    compute_currency_gaps,
    compute_lcr,
    compute_nsfr,
    compute_stressed_ladder,
)
from app.models import (
    Bank,
    BankFinancialFact,
    BankReportingPeriod,
    CanonicalPosition,
    CanonicalPositionSnapshot,
    ParamCapitalThreshold,
    ParamLcrRunoffRate,
    ParamLiquidityThreshold,
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
from app.services.jurisdictions import base_currency, regulator_name
from app.services.live_block import live_block
from app.services.live_types import (
    LiveModuleResult,
    findings_from_validations,
    worst_status,
)
from app.services.params import get_active_params

ENGINE_VERSION = "regulatory-liquidity-v1.0.0"
# v3 (2026-08-07): the snapshot gains the per-currency contractual ladders
# block (Phase 2 item 2 — FRM 16-17 currency gaps / USD funding stress).
INPUT_SCHEMA_VERSION = "bank-facts-v3"
OUTPUT_SCHEMA_VERSION = "liquidity-metrics-v1"
MODULE_LIQUIDITY = "liquidity"
BASELINE_SCENARIO = "baseline"
LIQUIDITY_SCENARIO_CODES = (
    "baseline",
    "idiosyncratic",
    "market_wide",
    "combined",
    # Cedi-depreciation-coupled FX funding stress (Phase 2 item 2): FX
    # run-off uplifts on the fact engine + fx_depreciation_pct on the
    # per-currency gap layer.
    "usd_funding_stress",
)

# LCR/NSFR is not one of the Guide's BSD forms (official BSD3 = Large
# Exposures); the preview is labelled by its honest return code.
BSD3_FORM_CODE = "LCR-NSFR"
BSD3_FORM_TITLE = "Liquidity Returns (LCR & NSFR)"


def preview_note(regulator: str) -> str:
    """Shared BSD preview disclaimer, jurisdiction-resolved by the caller."""
    return f"PREVIEW ONLY — This system does not file submissions with {regulator}."


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
    db: Session, ctx: TenantContext, bank_id: str, payload: RegulatoryRunCreate
) -> RegulatoryRunRead:
    _require_actor(ctx)
    bank = _get_bank_or_404(db, ctx, bank_id)
    period = _get_period_or_404(db, ctx, bank, payload.reporting_period_id)
    return _create_and_execute(db, ctx, bank, period, payload.scenario_code)


def run_all_liquidity_scenarios(
    db: Session, ctx: TenantContext, bank_id: str, payload: LiquidityScenarioBatchCreate
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
    bank_id: str,
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
    db: Session, ctx: TenantContext, bank_id: str, run_id: UUID
) -> RegulatoryRunRead:
    bank = _get_bank_or_404(db, ctx, bank_id)
    return _read_run(db, _run_or_404(db, ctx, bank.id, run_id))


def get_liquidity_dashboard(
    db: Session, ctx: TenantContext, bank_id: str, reporting_period_id: UUID | None = None
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
            for rule_code, passed, severity, message in _validation_rows(
                lcr, nsfr, params, base_currency(bank)
            )
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
        live=live_block(db, ctx, bank.id, period.id, MODULE_LIQUIDITY),
    )


def get_bsd3_preview(
    db: Session, ctx: TenantContext, bank_id: str, reporting_period_id: UUID
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

    metrics = _scalar_metrics(run)
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
    regulator = regulator_name(db, bank)
    return Bsd3PreviewRead(
        header=Bsd3HeaderRead(
            form_code=BSD3_FORM_CODE,
            form_title=BSD3_FORM_TITLE,
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
        hqla_rows=hqla_rows,
        outflow_rows=outflow_rows,
        inflow_rows=inflow_rows,
        summary_rows=summary_rows,
        nsfr=nsfr_section,
        validations=validations,
    )


@dataclass(frozen=True)
class LiquidityScenarioAnalysis:
    """One scenario's computed liquidity picture — engine outputs only."""

    lcr: LcrResult
    nsfr: NsfrResult
    params: LiquidityParams
    currency_gaps: CurrencyGapResult
    mismatch_limit: Decimal | None
    stressed_ladder: tuple[StressedLadder, ...]


def _execute_scenario_compute(  # noqa: PLR0913 - the official path hands over its loaded inputs
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    period: BankReportingPeriod,
    facts: list[BankFinancialFact],
    active: _ActiveLiquidityParams,
    currency_ladders: dict[str, dict[str, list[str] | str]],
    shocks: dict[str, Decimal],
    scenario_code: str,
) -> LiquidityScenarioAnalysis:
    """The scenario arithmetic shared by official runs and the workbench.

    Extracted from ``_create_and_execute`` so desk analysis and immutable
    regulatory runs are guaranteed to produce identical numbers for identical
    inputs. Pure with respect to run state: raises the module's domain errors,
    never writes a ``RegulatoryRun``.
    """
    engine_facts = tuple(_to_engine_fact(fact) for fact in facts)
    engine_params = _engine_params(active)
    if shocks:
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
    ladder_inputs = {
        currency: {
            "assets": _ladder_lists(ladder, "assets"),
            "liabilities": _ladder_lists(ladder, "liabilities"),
        }
        for currency, ladder in currency_ladders.items()
    }
    currency_gaps = compute_currency_gaps(
        ladder_inputs,
        base_currency(bank),
        shocks.get(SHOCK_FX_DEPRECIATION, Decimal(0)),
    )
    mismatch_limit = _currency_mismatch_limit(db, ctx, bank, period.period_end)
    runoff_schedule = {
        int(key.removeprefix(SHOCK_NMD_RUNOFF_PREFIX).removeprefix("h")): value
        for key, value in shocks.items()
        if key.startswith(SHOCK_NMD_RUNOFF_PREFIX)
    }
    stressed_ladder: tuple[StressedLadder, ...] = ()
    if runoff_schedule:
        stressed_ladder = compute_stressed_ladder(
            ladder_inputs,
            {
                currency: Decimal(str(ladder.get("demand_liabilities", "0")))
                for currency, ladder in currency_ladders.items()
            },
            runoff_schedule,
        )
    return LiquidityScenarioAnalysis(
        lcr=lcr,
        nsfr=nsfr,
        params=engine_params,
        currency_gaps=currency_gaps,
        mismatch_limit=mismatch_limit,
        stressed_ladder=stressed_ladder,
    )


def compute_scenario_analysis(  # noqa: PLR0913 - the workbench seam names its full scope
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    period: BankReportingPeriod,
    shocks: dict[str, Decimal],
    scenario_code: str = "analysis",
) -> LiquidityScenarioAnalysis:
    """Workbench seam: compute one scenario without persisting anything."""
    facts = _load_facts(db, ctx, bank, period)
    active = _load_active_params(db, ctx, bank, period.period_end)
    currency_ladders = _currency_ladders(db, ctx, bank, period)
    return _execute_scenario_compute(
        db, ctx, bank, period, facts, active, currency_ladders, shocks, scenario_code
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
    currency_ladders = _currency_ladders(db, ctx, bank, period)
    snapshot = _build_snapshot(
        bank, period, scenario_code, facts, active, shocks, currency_ladders
    )

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
        if scenario_code != BASELINE_SCENARIO and not shocks:
            raise LiquidityRunError(
                "missing_parameter",
                f"No liquidity stress shocks are configured for scenario '{scenario_code}'.",
                {"scenario_code": scenario_code},
            )
        analysis = _execute_scenario_compute(
            db, ctx, bank, period, facts, active, currency_ladders, shocks, scenario_code
        )
        _persist_success(
            db,
            ctx,
            run,
            analysis.lcr,
            analysis.nsfr,
            analysis.params,
            base_currency(bank),
            analysis.currency_gaps,
            analysis.mismatch_limit,
            analysis.stressed_ladder,
        )
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


def _persist_success(  # noqa: PLR0913, PLR0915
    db: Session,
    ctx: TenantContext,
    run: RegulatoryRun,
    lcr: LcrResult,
    nsfr: NsfrResult,
    params: LiquidityParams,
    currency: str,
    currency_gaps: CurrencyGapResult,
    mismatch_limit: Decimal | None,
    stressed_ladder: tuple[StressedLadder, ...] = (),
) -> None:
    run.metrics = {
        "lcr_pct": str(lcr.lcr_pct),
        "nsfr_pct": str(nsfr.nsfr_pct),
        "hqla_total_ghs": str(lcr.hqla_total),
        "net_outflows_30d_ghs": str(lcr.net_outflows_total),
        "asf_total_ghs": str(nsfr.asf_total),
        "rsf_total_ghs": str(nsfr.rsf_total),
        "fx_funding_gap_ghs": str(currency_gaps.fx_funding_gap),
        "fx_share_of_liabilities_pct": str(currency_gaps.fx_share_of_liabilities_pct),
        "stressed_fx_funding_gap_ghs": str(currency_gaps.stressed_fx_funding_gap),
        "fx_depreciation_pct": str(currency_gaps.fx_depreciation_pct),
        "currency_gaps": [
            {
                "currency": gap.currency,
                "assets": [str(v) for v in gap.assets],
                "liabilities": [str(v) for v in gap.liabilities],
                "net": [str(v) for v in gap.net],
                "cumulative": [str(v) for v in gap.cumulative],
                "assets_total": str(gap.assets_total),
                "liabilities_total": str(gap.liabilities_total),
                "net_total": str(gap.net_total),
                "stressed_liabilities_total": str(gap.stressed_liabilities_total),
                "stressed_net_total": str(gap.stressed_net_total),
            }
            for gap in currency_gaps.gaps
        ],
    }
    if stressed_ladder:
        # LRMD para 50-54: the behaviourally-modified stress ladder.
        run.metrics["stressed_ladder"] = [
            {
                "currency": entry.currency,
                "contractual_liabilities": [str(v) for v in entry.contractual_liabilities],
                "stressed_liabilities": [str(v) for v in entry.stressed_liabilities],
                "stressed_net": [str(v) for v in entry.stressed_net],
                "stressed_cumulative": [str(v) for v in entry.stressed_cumulative],
                "demand_deposits": str(entry.demand_deposits),
                "stable_core": str(entry.stable_core),
            }
            for entry in stressed_ladder
        ]
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
        (
            *_validation_rows(lcr, nsfr, params, currency),
            *_currency_gap_validations(currency_gaps, mismatch_limit),
        ),
        start=1,
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
    lcr: LcrResult, nsfr: NsfrResult, params: LiquidityParams, currency: str
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
            f"{lcr.gross_inflows_total} {currency} were capped at "
            f"{lcr.capped_inflows_total} {currency}."
        )
    else:
        cap_message = (
            f"The {_pct_text(params.inflow_cap_pct)}% inflow cap did not bind: gross inflows "
            f"of {lcr.gross_inflows_total} {currency} are below the cap of "
            f"{lcr.inflow_cap_amount} {currency}."
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



def _currency_gap_validations(
    currency_gaps: CurrencyGapResult, mismatch_limit: Decimal | None
) -> list[tuple[str, bool, str, str]]:
    """Board per-currency mismatch limit checks (LMTD para 11(d)).

    Applies only when the Board has adopted ``currency_mismatch_limit_pct``
    in the threshold register: the worst negative cumulative gap of each
    currency, as a percentage of that currency's liabilities, must not
    exceed the limit. Without a Board row no check is invented.
    """
    if mismatch_limit is None:
        return []
    rows: list[tuple[str, bool, str, str]] = []
    for gap in currency_gaps.gaps:
        if gap.liabilities_total <= 0:
            continue
        worst = min(gap.cumulative)
        if worst >= 0:
            rows.append(
                (
                    f"currency_mismatch_{gap.currency.lower()}",
                    True,
                    "info",
                    f"{gap.currency}: no negative cumulative gap.",
                )
            )
            continue
        breach_pct = (-worst) / gap.liabilities_total * Decimal("100")
        passed = breach_pct <= mismatch_limit
        rows.append(
            (
                f"currency_mismatch_{gap.currency.lower()}",
                passed,
                "info" if passed else "warning",
                (
                    f"{gap.currency}: worst cumulative gap {worst} is "
                    f"{breach_pct.quantize(Decimal('0.0001'))}% of currency "
                    f"liabilities against the Board limit of {mismatch_limit}%."
                ),
            )
        )
    return rows


def _scalar_metrics(run: RegulatoryRun) -> dict[str, Decimal]:
    """run.metrics scalars as Decimals, skipping structured blocks
    (``currency_gaps`` is a list of per-currency dicts as of v3)."""
    return {
        key: Decimal(str(value))
        for key, value in run.metrics.items()
        if not isinstance(value, (list, dict))
    }


def _metrics_from_results(
    lcr: LcrResult, nsfr: NsfrResult, currency_gaps: CurrencyGapResult | None = None
) -> LiquidityMetricsRead:
    return LiquidityMetricsRead(
        lcr_pct=lcr.lcr_pct,
        lcr_status=lcr.status,
        nsfr_pct=nsfr.nsfr_pct,
        nsfr_status=nsfr.status,
        hqla_total_ghs=lcr.hqla_total,
        net_outflows_30d_ghs=lcr.net_outflows_total,
        asf_total_ghs=nsfr.asf_total,
        rsf_total_ghs=nsfr.rsf_total,
        fx_funding_gap_ghs=currency_gaps.fx_funding_gap if currency_gaps else None,
        fx_share_of_liabilities_pct=(
            currency_gaps.fx_share_of_liabilities_pct if currency_gaps else None
        ),
        stressed_fx_funding_gap_ghs=(
            currency_gaps.stressed_fx_funding_gap if currency_gaps else None
        ),
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
    metrics = _scalar_metrics(run)
    return LiquidityMetricsRead(
        lcr_pct=metrics["lcr_pct"],
        lcr_status=statuses.get("lcr_pct", "red"),  # type: ignore[arg-type]
        nsfr_pct=metrics["nsfr_pct"],
        nsfr_status=statuses.get("nsfr_pct", "red"),  # type: ignore[arg-type]
        hqla_total_ghs=metrics["hqla_total_ghs"],
        net_outflows_30d_ghs=metrics["net_outflows_30d_ghs"],
        asf_total_ghs=metrics["asf_total_ghs"],
        rsf_total_ghs=metrics["rsf_total_ghs"],
        fx_funding_gap_ghs=metrics.get("fx_funding_gap_ghs"),
        fx_share_of_liabilities_pct=metrics.get("fx_share_of_liabilities_pct"),
        stressed_fx_funding_gap_ghs=metrics.get("stressed_fx_funding_gap_ghs"),
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


# Dashboard trends show a trailing window, not the bank's full period history. With
# 10 years of monthly history (~120 periods) and few stored runs, recomputing every
# period inline on each load cost ~600 queries / ~25s; a trailing year is both fast
# and a readable sparkline. Tune here if a longer horizon is wanted.
_TREND_MAX_POINTS = 13


def _build_trend(
    db: Session, ctx: TenantContext, bank: Bank, periods: list[BankReportingPeriod]
) -> list[LiquidityTrendPointRead]:
    points: list[LiquidityTrendPointRead] = []
    for period in periods[-_TREND_MAX_POINTS:]:
        run = _latest_succeeded_baseline_run(db, ctx, bank, period.id)
        if run is not None:
            metrics = _scalar_metrics(run)
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


def current_input_hash(
    db: Session, ctx: TenantContext, bank: Bank, period: BankReportingPeriod
) -> str | None:
    """The baseline input hash of the current canonical state for this period.

    Built with the SAME snapshot + hash the immutable baseline run uses, so the
    freshness service can compare it against the latest official run's hash.
    """
    facts = _load_facts(db, ctx, bank, period)
    if not facts:
        return None
    active = _load_active_params(db, ctx, bank, period.period_end)
    currency_ladders = _currency_ladders(db, ctx, bank, period)
    snapshot = _build_snapshot(
        bank, period, BASELINE_SCENARIO, facts, active, {}, currency_ladders
    )
    return _snapshot_hash(snapshot)


def compute_live(
    db: Session, ctx: TenantContext, bank: Bank, period: BankReportingPeriod
) -> LiveModuleResult:
    """Cheap baseline live view — reuses the dashboard's unstored-branch path
    (``_compute_inline``/``_validation_rows``) and creates no RegulatoryRun."""
    lcr, nsfr, params = _compute_inline(db, ctx, bank, period)
    metrics = {
        "lcr_pct": str(lcr.lcr_pct),
        "nsfr_pct": str(nsfr.nsfr_pct),
        "hqla_total_ghs": str(lcr.hqla_total),
        "net_outflows_30d_ghs": str(lcr.net_outflows_total),
        "asf_total_ghs": str(nsfr.asf_total),
        "rsf_total_ghs": str(nsfr.rsf_total),
    }
    status = worst_status(lcr.status, nsfr.status)
    findings = findings_from_validations(
        _validation_rows(lcr, nsfr, params, base_currency(bank)), status
    )
    return LiveModuleResult(
        metrics=metrics,
        status=status,
        input_hash=current_input_hash(db, ctx, bank, period),
        findings=findings,
    )


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



# --- Per-currency contractual ladders (Phase 2 item 2; FRM 16-17) -----------
#
# Five LMTD horizons in cedi equivalents from the canonical book — the same
# current-generation accepted/warning slice the LMT return derives from.
# Undated demand-natured items (cash; current/call/savings deposits) sit in
# the shortest bucket; other undated in the longest. Stringified and
# canonically ordered for the value-based input hash.
_LADDER_HORIZON_DAYS = (30, 91, 182, 365, None)
_CCY_ASSET_TYPES = ("LOAN", "SECURITY_HOLDING", "CASH", "INTERBANK_PLACEMENT", "OTHER_ASSET")
_CCY_LIABILITY_TYPES = ("DEPOSIT", "INTERBANK_BORROWING", "OTHER_LIABILITY")
_CCY_DERIVATIVES = ("DERIVATIVE", "FX_HEDGE", "INTEREST_RATE_SWAP")
_CCY_DEMAND_DEPOSITS = ("CURRENT", "CALL", "SAVINGS")
_CCY_STATUSES = ("accepted", "warning")


def _ladder_bucket_index(
    maturity: date | None, as_of: date, *, on_demand: bool
) -> int:
    if maturity is None:
        return 0 if on_demand else len(_LADDER_HORIZON_DAYS) - 1
    days = (maturity - as_of).days
    for index, upper in enumerate(_LADDER_HORIZON_DAYS):
        if upper is None or days <= upper:
            return index
    return len(_LADDER_HORIZON_DAYS) - 1


def _currency_ladders(
    db: Session, ctx: TenantContext, bank: Bank, period: BankReportingPeriod
) -> dict[str, dict[str, list[str] | str]]:
    records = db.execute(
        select(CanonicalPositionSnapshot, CanonicalPosition)
        .join(CanonicalPosition, CanonicalPositionSnapshot.position_id == CanonicalPosition.id)
        .where(
            CanonicalPositionSnapshot.organization_id == ctx.organization_id,
            CanonicalPositionSnapshot.bank_id == bank.id,
            CanonicalPositionSnapshot.as_of_date == period.period_end,
            CanonicalPositionSnapshot.superseded_by.is_(None),
            CanonicalPositionSnapshot.validation_status.in_(_CCY_STATUSES),
            CanonicalPosition.position_type.in_(
                (*_CCY_ASSET_TYPES, *_CCY_LIABILITY_TYPES, *_CCY_DERIVATIVES)
            ),
        )
    ).all()
    base = base_currency(bank)
    buckets = len(_LADDER_HORIZON_DAYS)
    ladders: dict[str, dict[str, list[Decimal]]] = {}
    for snapshot, position in records:
        attributes = snapshot.attributes or {}
        raw = attributes.get("balance_ghs")
        if raw not in (None, ""):
            amount = Decimal(str(raw))
        elif position.currency == base:
            amount = Decimal(str(snapshot.balance or 0))
        else:
            # No ingested conversion: contributes zero, never a made-up rate
            # (mirrors fact_derivation and the LMT generator).
            continue
        kind = position.position_type
        if kind in _CCY_DERIVATIVES:
            side = "assets" if amount >= 0 else "liabilities"
            amount = abs(amount)
        elif kind in _CCY_ASSET_TYPES:
            side = "assets"
        else:
            side = "liabilities"
        on_demand = kind == "CASH" or (
            kind == "DEPOSIT"
            and (snapshot.deposit_account_type or "").upper() in _CCY_DEMAND_DEPOSITS
        )
        index = _ladder_bucket_index(
            snapshot.contractual_maturity, period.period_end, on_demand=on_demand
        )
        ladder = ladders.setdefault(
            position.currency,
            {
                "assets": [Decimal(0)] * buckets,
                "liabilities": [Decimal(0)] * buckets,
                "demand": [Decimal(0)],
            },
        )
        ladder[side][index] += amount
        if kind == "DEPOSIT" and on_demand:
            ladder["demand"][0] += amount
    return {
        currency: {
            "assets": [str(value) for value in ladder["assets"]],
            "liabilities": [str(value) for value in ladder["liabilities"]],
            "demand_liabilities": str(ladder["demand"][0]),
        }
        for currency, ladder in sorted(ladders.items())
    }


def _ladder_lists(ladder: dict[str, list[str] | str], key: str) -> list[Decimal]:
    values = ladder.get(key, [])
    assert isinstance(values, list)  # demand_liabilities is the only scalar key
    return [Decimal(value) for value in values]


def _currency_mismatch_limit(
    db: Session, ctx: TenantContext, bank: Bank, as_of: date
) -> Decimal | None:
    """Board per-currency mismatch limit (LMTD para 11(d)), when adopted."""
    rows = get_active_params(
        db, ctx.organization_id, bank.jurisdiction_code, ParamLiquidityThreshold, as_of
    )
    for row in rows:
        if row.institution_class == "bank" and row.threshold_code == "currency_mismatch_limit_pct":
            return Decimal(str(row.threshold_pct))
    return None


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
    currency_ladders: dict[str, dict[str, list[str] | str]],
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
        "facts": sorted(
            (
                {
                    "fact_group": fact.fact_group,
                    "category": fact.category,
                    "amount": str(fact.amount),
                    "hqla_level": fact.hqla_level,
                    "side": fact.attributes.get("side"),
                    "cash_derived": fact.attributes.get("source") == "cash",
                }
                for fact in facts
            ),
            key=lambda entry: json.dumps(entry, sort_keys=True),
        ),
        "parameters": {
            "outflow_runoff_rates_pct": _stringified(active.outflow_rates),
            "inflow_rates_pct": _stringified(active.inflow_rates),
            "asf_weights_pct": _stringified(active.asf_weights),
            "rsf_weights_pct": _stringified(active.rsf_weights),
            "thresholds_pct": _stringified(active.thresholds),
        },
        "shocks": _stringified(shocks),
        # v3: per-currency contractual ladders (five LMTD horizons, cedi
        # equivalents) — canonical keys sorted, values stringified.
        "currency_ladders": currency_ladders,
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


def _run_or_404(db: Session, ctx: TenantContext, bank_id: str, run_id: UUID) -> RegulatoryRun:
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


def liquidity_breach_multiplier(  # noqa: PLR0914 - one bounded frontier search
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    period: BankReportingPeriod,
    scenario_code: str = "combined",
) -> dict[str, Any]:
    """Reverse stress (Phase 2 item 4): the smallest severity multiplier k at
    which the LCR breaches its minimum, scaling the named scenario's shocks.

    k interpolates between baseline parameters (k=0) and the configured
    scenario (k=1), extrapolating beyond: run-off rates move linearly from
    the base rate toward the shocked rate (capped at 100), the inflow
    multiplier from 1 toward the shocked multiplier (floored at 0), and the
    HQLA haircut linearly (capped at 100). Behavioural/FX keys do not enter
    the LCR and are excluded. Deterministic bisection to 0.05 precision over
    k in (0, 5]; inline computation, no stored runs per probe.
    """
    facts = _load_facts(db, ctx, bank, period)
    if not facts:
        raise LiquidityRunError(
            "financial_facts_missing",
            "The reporting period has no financial facts to analyze.",
            {"reporting_period_id": str(period.id)},
        )
    active = _load_active_params(db, ctx, bank, period.period_end)
    shocks = _load_shocks(db, ctx, bank, scenario_code, period.period_end)
    if not shocks:
        raise LiquidityRunError(
            "missing_parameter",
            f"No liquidity stress shocks are configured for scenario '{scenario_code}'.",
            {"scenario_code": scenario_code},
        )
    engine_facts = tuple(_to_engine_fact(fact) for fact in facts)
    engine_params = _engine_params(active)
    hundred = Decimal("100")
    one = Decimal("1")

    def scaled_shocks(k: Decimal) -> dict[str, Decimal]:
        scaled: dict[str, Decimal] = {}
        for key, value in shocks.items():
            if key.startswith("runoff:"):
                base = engine_params.outflow_rates.get(key.removeprefix("runoff:"), _ZERO)
                scaled[key] = min(hundred, max(_ZERO, base + (value - base) * k))
            elif key == "inflow_multiplier":
                scaled[key] = max(_ZERO, one + (value - one) * k)
            elif key == "hqla_securities_haircut_pct":
                scaled[key] = min(hundred, max(_ZERO, value * k))
            elif key.startswith("asf:") or key == "rsf:securities_weight_override":
                scaled[key] = value  # NSFR shocks do not move the LCR frontier
            # fx_depreciation_pct / nmd_runoff:* never reach the fact engine.
        return scaled

    def lcr_at(k: Decimal) -> Decimal | None:
        stressed_facts, stressed_params = apply_liquidity_stress(
            scenario_code, engine_facts, engine_params, scaled_shocks(k)
        )
        try:
            return compute_lcr(stressed_facts, stressed_params).lcr_pct
        except LiquidityComputationError:
            return None  # degenerate (net outflow <= 0): treated as no breach

    minimum = engine_params.lcr_min_pct
    k_max = Decimal("5")
    precision = Decimal("0.05")
    lcr_max = lcr_at(k_max)
    baseline_lcr = lcr_at(_ZERO)
    if lcr_max is None or lcr_max >= minimum:
        return {
            "breached": False,
            "scenario_code": scenario_code,
            "lcr_min_pct": str(minimum),
            "baseline_lcr_pct": str(baseline_lcr) if baseline_lcr is not None else None,
            "lcr_at_k_max_pct": str(lcr_max) if lcr_max is not None else None,
            "k_max": str(k_max),
        }
    low, high = _ZERO, k_max
    while high - low > precision:
        mid = (low + high) / 2
        value = lcr_at(mid)
        if value is not None and value < minimum:
            high = mid
        else:
            low = mid
    frontier = high.quantize(precision)
    frontier_lcr = lcr_at(frontier)
    return {
        "breached": True,
        "scenario_code": scenario_code,
        "lcr_min_pct": str(minimum),
        "baseline_lcr_pct": str(baseline_lcr) if baseline_lcr is not None else None,
        "breach_multiplier": str(frontier),
        "lcr_at_breach_pct": str(frontier_lcr) if frontier_lcr is not None else None,
    }
