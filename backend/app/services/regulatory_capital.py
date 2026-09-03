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
from collections.abc import Sequence
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.errors import ModuleDataUnavailable
from app.domain.capital.ecl import (
    BASE_SCENARIO,
    EclAssumption,
    EclExposure,
    EclResult,
    EclScenario,
    compute_ecl,
)
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
    FinancialFactRow,
    ParamCapitalThreshold,
    ParamCrmHaircut,
    ParamEclAssumption,
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
from app.services import (
    filing_reconciliation,
    regulatory_dashboard_batching,
    regulatory_parameters,
    sdi_capital,
    sdi_capital_checks,
)
from app.services.audit import record_event
from app.services.institution_types import institution_class as _resolve_institution_class
from app.services.jurisdictions import base_currency, regulator_name
from app.services.live_block import live_block
from app.services.live_state import current_fact_period_or_409, current_snapshot, load_current_facts
from app.services.live_types import (
    LiveFindingSpec,
    LiveModuleResult,
    findings_from_validations,
    worst_status,
)
from app.services.params import PrefetchedActiveParams, get_active_params, prefetch_active_params
from app.services.regulatory_liquidity import get_regulatory_run, preview_note

#: Bumped 2026-08-22 (forensic re-audit D-5) from ``v1.0.0``. MAJOR, because the
#: change moves a FILED figure over unchanged inputs: the Basel II ¶649 basic-
#: indicator base correction re-based operational RWA, taking Sample-scale
#: ``car_pct`` 30.486772 → 29.367617. Four distinct capital-metric generations
#: are already sealed on the primary under the single string ``v1.0.0`` — two
#: runs there can carry identical ``input_hash``, identical
#: ``input_schema_version`` and identical ``engine_version`` and still disagree
#: on ``car_pct``, which is precisely what
#: docs/audit/10_calculation_versioning.md says cannot happen. Historical rows
#: are NOT rewritten: a stored ``v1.0.0`` now means "one of the pre-2026-08-22
#: generations, not individually identifiable", and that is a fact about those
#: runs, not something a backfill could honestly change.
#:
#: THE RULE, so the next change does not have to re-derive it: MAJOR when the
#: engine would produce a different number from the same ``input_hash``; MINOR
#: when it adds an output, a line item or a diagnostic without moving an
#: existing figure; PATCH for anything a filed figure cannot see.
ENGINE_VERSION = "regulatory-capital-v2.0.0"
INPUT_SCHEMA_VERSION = "bank-facts-v2"
OUTPUT_SCHEMA_VERSION = "capital-metrics-v1"
MODULE_CAPITAL = "capital"
BASELINE_SCENARIO = "baseline"
CAPITAL_SCENARIO_CODES = ("baseline", "mild", "moderate", "severe")

# The Capital Adequacy Return's OFFICIAL BoG form is BSD5A ("CAR FORMAT"); the
# earlier "BSD-2" label pre-dated the official templates (bog_forms/).
BSD2_FORM_CODE = "BSD5A"
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
    "crm_collateral",
    "ecl_exposure",
    "loan_exposure",
    "market_risk",
    "off_balance",
    "operational_income",
    "securities",
)

# Phase 2 item 8: forward-looking ECL conditioning shocks — consumed by the
# ECL engine, never passed to run_capital_stress (which rejects unknown keys).
ECL_CONDITIONING_KEYS = ("ecl_pd_multiplier", "ecl_lgd_multiplier")

# Phase 2 item 9: Basel II comprehensive-approach supervisory haircuts
# (¶151 table; conservative representative pick per class — 10-business-day
# holding period, the table's upper band where a range applies). These are
# CODE defaults, overridable per class through the effective-dated
# ``param_crm_haircut`` register; a class absent from both gets zero
# recognition — a haircut is never invented.
DEFAULT_CRM_HAIRCUTS: dict[str, Decimal] = {
    "CASH": Decimal("0"),
    "GOLD": Decimal("15"),
    "SOVEREIGN_DEBT": Decimal("4"),
    "BANK_DEBT": Decimal("8"),
    "CORPORATE_DEBT": Decimal("8"),
    "EQUITY_MAIN_INDEX": Decimal("15"),
    "EQUITY_OTHER": Decimal("25"),
}
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
    # Phase 2 items 8/9. crm_haircuts = code defaults overlaid by register
    # rows; ecl_assumptions empty until the bank configures its PD/LGD set
    # (the engine then falls back to ingested provisions).
    crm_haircuts: dict[str, Decimal] = dataclass_field(default_factory=dict)
    ecl_assumptions: tuple[EclAssumption, ...] = ()
    # SDI Phase E (docs/sdi.md §4.2): the institution class selects the capital
    # regime (bank BoG CRD vs SDI simplified s.29), and the control-plane CAR
    # floor is the fallback when the tenant has no board threshold row. Defaults
    # keep every existing constructor on the byte-identical bank path.
    institution_class: str = "bank"
    car_min_fallback: Decimal | None = None
    # The GOVERNED s.29 risk-weighted-asset scope (``sdi_capital.resolve_rwa_scope``),
    # resolved for an SDI and None for a bank. It is the ONE authority for which risk
    # classes an SDI charges for; this module consumes it and never restates it.
    rwa_scope: sdi_capital.SdiRwaScope | None = None


@dataclass(frozen=True)
class _CapitalDashboardBatch:
    runs: dict[UUID, RegulatoryRun]
    facts: dict[UUID, list[BankFinancialFact]]
    weights: PrefetchedActiveParams[ParamRiskWeight]
    thresholds: PrefetchedActiveParams[ParamCapitalThreshold]
    crm: PrefetchedActiveParams[ParamCrmHaircut]
    ecl: PrefetchedActiveParams[ParamEclAssumption]
    governed: regulatory_parameters.PrefetchedParameterResolver


@dataclass(frozen=True)
class CapitalScenarioAnalysis:
    """One scenario's computed capital picture — engine outputs only."""

    rwa: RwaResult
    ratios: CapitalRatiosResult
    stress: CapitalStressResult | None
    params: CapitalParams
    ecl: EclResult | None


def _execute_scenario_compute(
    facts: Sequence[FinancialFactRow],
    active: _ActiveCapitalParams,
    shocks: dict[str, Decimal],
    scenario_code: str,
    period: BankReportingPeriod,
) -> CapitalScenarioAnalysis:
    """The scenario arithmetic shared by official runs and the workbench.

    Empty stress shocks (net of the ECL conditioning keys) means the point-in-
    time baseline; otherwise the four-quarter stress path. Raises the module's
    domain errors; never writes a ``RegulatoryRun``.
    """
    if not facts:
        raise CapitalRunError(
            "financial_facts_missing",
            "The reporting period has no financial facts to analyze.",
            {"reporting_period_id": str(period.id)},
        )
    engine_facts = tuple(_to_engine_fact(fact) for fact in facts)
    engine_params = _engine_params(active)
    # ECL conditioning keys are the ECL engine's, never the stress engine's
    # (which rejects unknown shocks).
    stress_shocks = {
        key: value for key, value in shocks.items() if key not in ECL_CONDITIONING_KEYS
    }
    ecl = _modeled_ecl(engine_facts, active, shocks)
    gp_override = ecl.general_ecl if ecl is not None else None
    if not stress_shocks:
        rwa = compute_rwa(engine_facts, engine_params)
        ratios = compute_capital_ratios(engine_facts, rwa, engine_params, gp_override)
        return CapitalScenarioAnalysis(
            rwa=rwa, ratios=ratios, stress=None, params=engine_params, ecl=ecl
        )
    stress = run_capital_stress(
        scenario_code, engine_facts, engine_params, stress_shocks, gp_override
    )
    return CapitalScenarioAnalysis(
        rwa=stress.rwa, ratios=stress.ratios, stress=stress, params=engine_params, ecl=ecl
    )


def compute_scenario_analysis(  # noqa: PLR0913 - the workbench seam names its full scope
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    period: BankReportingPeriod,
    shocks: dict[str, Decimal],
    scenario_code: str = "analysis",
) -> CapitalScenarioAnalysis:
    """Workbench seam: compute one scenario without persisting anything."""
    facts = _load_facts(db, ctx, bank, period)
    active = _load_active_params(db, ctx, bank, period.period_end)
    return _execute_scenario_compute(facts, active, shocks, scenario_code, period)


def create_capital_run(
    db: Session, ctx: TenantContext, bank_id: str, payload: RegulatoryRunCreate
) -> RegulatoryRunRead:
    _require_actor(ctx)
    bank = _get_bank_or_404(db, ctx, bank_id)
    period = _get_period_or_404(db, ctx, bank, payload.reporting_period_id)
    # Audit 2026-08-22 D-3(b): this endpoint mints the same immutable runs as an
    # activation but never went through ``derive_facts``, so the balance-sheet
    # control had no say over CAR, CET1 or RWA reached this way.
    filing_reconciliation.assert_filing_reconciled(
        db, ctx, bank, as_of=period.period_end, period_id=period.id, purpose="official_run"
    )
    # Audit 2026-08-22 D-19: an SDI's risk-weighted-asset SCOPE is a regulatory
    # determination. Refuse the mint before the run row exists rather than sealing
    # a CAR onto the platform's own placeholder. A no-op for a bank.
    sdi_capital.assert_official_rwa_scope_governed(db, bank, period.period_end)
    return _create_and_execute(db, ctx, bank, period, payload.scenario_code)


def run_all_capital_scenarios(
    db: Session, ctx: TenantContext, bank_id: str, payload: CapitalScenarioBatchCreate
) -> RegulatoryRunBatchRead:
    _require_actor(ctx)
    bank = _get_bank_or_404(db, ctx, bank_id)
    period = _get_period_or_404(db, ctx, bank, payload.reporting_period_id)
    # See ``create_capital_run``: the 22-scenario batch is the same mint.
    filing_reconciliation.assert_filing_reconciled(
        db, ctx, bank, as_of=period.period_end, period_id=period.id, purpose="official_run"
    )
    sdi_capital.assert_official_rwa_scope_governed(db, bank, period.period_end)
    runs = [
        _create_and_execute(db, ctx, bank, period, scenario_code)
        for scenario_code in CAPITAL_SCENARIO_CODES
    ]
    return RegulatoryRunBatchRead(bank_id=bank.id, reporting_period_id=period.id, runs=runs)


def get_capital_dashboard(
    db: Session, ctx: TenantContext, bank_id: str, reporting_period_id: UUID | None = None
) -> CapitalDashboardRead:
    bank = _get_bank_or_404(db, ctx, bank_id)
    periods = _list_periods_ascending(db, ctx, bank)
    if reporting_period_id is None:
        period = current_fact_period_or_409(db, ctx, bank, MODULE_CAPITAL)
    else:
        period = _get_period_or_404(db, ctx, bank, reporting_period_id)

    batch = _prefetch_dashboard_batch(db, ctx, bank, periods, extra_period=period)
    latest_run = batch.runs.get(period.id) if reporting_period_id is not None else None
    active = _active_params_from_batch(db, ctx, bank, period.period_end, batch)
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
        rwa, ratios, engine_params = _compute_inline_or_409(
            db, ctx, bank, period, batch=batch, active=active
        )
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
                ratios, engine_params, None, base_currency(bank)
            )
        ]
        stored = False

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
        trend=_build_trend(db, ctx, bank, periods, batch=batch),
        buffers=_buffers_or_409(active, metrics.car_pct),
        validations=validations,
        live=live_block(db, ctx, bank.id, MODULE_CAPITAL),
    )


def get_capital_structure(
    db: Session, ctx: TenantContext, bank_id: str, reporting_period_id: UUID | None = None
) -> CapitalStructureRead:
    if reporting_period_id is None:
        dashboard = get_capital_dashboard(db, ctx, bank_id)
        return CapitalStructureRead(
            bank_id=dashboard.bank.id,
            reporting_period_id=dashboard.period.id,
            run_id=None,
            source="live",
            **dashboard.capital_structure.model_dump(),
        )
    bank, period, run = _baseline_run_or_409(
        db, ctx, bank_id, reporting_period_id, artifact="the capital structure"
    )
    sections = _sections_from_run(db, run)
    summary = _structure_from_lines(sections.get("capital_component", []))
    return CapitalStructureRead(
        bank_id=bank.id,
        reporting_period_id=period.id,
        run_id=run.id,
        source="official",
        **summary.model_dump(),
    )


def get_rwa_breakdown(
    db: Session, ctx: TenantContext, bank_id: str, reporting_period_id: UUID | None = None
) -> RwaBreakdownRead:
    if reporting_period_id is None:
        dashboard = get_capital_dashboard(db, ctx, bank_id)
        composition = dashboard.rwa_composition
        return RwaBreakdownRead(
            bank_id=dashboard.bank.id,
            reporting_period_id=dashboard.period.id,
            run_id=None,
            source="live",
            credit_rwa_ghs=composition.credit_rwa_ghs,
            market_rwa_ghs=composition.market_rwa_ghs,
            operational_rwa_ghs=composition.operational_rwa_ghs,
            total_rwa_ghs=composition.total_rwa_ghs,
            credit_lines=composition.credit_lines,
            market_lines=[],
            operational_lines=[],
        )
    bank, period, run = _baseline_run_or_409(
        db, ctx, bank_id, reporting_period_id, artifact="the RWA breakdown"
    )
    sections = _sections_from_run(db, run)
    metrics = _decimal_metrics(run)
    return RwaBreakdownRead(
        bank_id=bank.id,
        reporting_period_id=period.id,
        run_id=run.id,
        source="official",
        credit_rwa_ghs=metrics["credit_rwa_ghs"],
        market_rwa_ghs=metrics["market_rwa_ghs"],
        operational_rwa_ghs=metrics["operational_rwa_ghs"],
        total_rwa_ghs=metrics["total_rwa_ghs"],
        credit_lines=sections.get("credit_rwa", []),
        market_lines=sections.get("market_rwa", []),
        operational_lines=sections.get("operational_rwa", []),
    )


def get_bsd2_preview(
    db: Session, ctx: TenantContext, bank_id: str, reporting_period_id: UUID
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
            description=_tier2_row_description(
                line, cap_pct, metrics["credit_rwa_ghs"], base_currency(bank)
            ),
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
        if scenario_code != BASELINE_SCENARIO:
            stress_only = {
                key: value for key, value in shocks.items() if key not in ECL_CONDITIONING_KEYS
            }
            if not stress_only:
                raise CapitalRunError(
                    "missing_parameter",
                    f"No capital stress shocks are configured for scenario '{scenario_code}'.",
                    {"scenario_code": scenario_code},
                )
        analysis = _execute_scenario_compute(facts, active, shocks, scenario_code, period)
        _persist_success(
            db,
            ctx,
            run,
            analysis.rwa,
            analysis.ratios,
            analysis.params,
            analysis.stress,
            base_currency(bank),
            analysis.ecl,
        )
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


def _modeled_ecl(
    facts: tuple[CapitalFact, ...],
    active: _ActiveCapitalParams,
    shocks: dict[str, Decimal],
) -> EclResult | None:
    """IFRS 9 modeled ECL (item 8) — active only when BOTH staged exposures
    and Board-configured assumptions exist; otherwise the run keeps the
    ingested-provisions path untouched."""
    exposures = []
    for fact in facts:
        if fact.fact_group != "ecl_exposure":
            continue
        segment, _, stage_token = fact.category.rpartition(":stage")
        if not segment or not stage_token.isdigit():
            continue
        exposures.append(EclExposure(segment=segment, stage=int(stage_token), ead=fact.amount))
    if not exposures or not active.ecl_assumptions:
        return None
    pd_multiplier = shocks.get("ecl_pd_multiplier")
    lgd_multiplier = shocks.get("ecl_lgd_multiplier")
    if pd_multiplier is not None or lgd_multiplier is not None:
        scenarios = (
            EclScenario(
                code="conditioned",
                weight_pct=Decimal("100"),
                pd_multiplier=pd_multiplier if pd_multiplier is not None else Decimal("1"),
                lgd_multiplier=lgd_multiplier if lgd_multiplier is not None else Decimal("1"),
            ),
        )
    else:
        scenarios = (BASE_SCENARIO,)
    return compute_ecl(exposures, active.ecl_assumptions, scenarios)


def _persist_success(  # noqa: PLR0913
    db: Session,
    ctx: TenantContext,
    run: RegulatoryRun,
    rwa: RwaResult,
    ratios: CapitalRatiosResult,
    params: CapitalParams,
    stress: CapitalStressResult | None,
    currency: str,
    ecl: EclResult | None = None,
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
    if ecl is not None:
        # Item 8: modeled IFRS 9 allowances. Stage 1+2 replaced the ingested
        # general-provisions component in this run's Tier 2 (still capped).
        metrics["ecl_total_ghs"] = str(ecl.total_ecl)
        metrics["ecl_general_ghs"] = str(ecl.general_ecl)
        metrics["ecl_specific_ghs"] = str(ecl.specific_ecl)
        for stage in (1, 2, 3):
            metrics[f"ecl_stage{stage}_ghs"] = str(ecl.stage_totals.get(stage, Decimal("0")))
        if ecl.uncovered:
            metrics["ecl_uncovered_segments"] = ",".join(
                f"{segment}:stage{stage}" for segment, stage in ecl.uncovered
            )
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
    # A stress run persists NO end-state capital ratio here, deliberately. A
    # RegulatoryMetricResult row carries a threshold and a compliance status, and
    # the STRESS-PACK used to copy both verbatim into a filed return
    # (regulatory_reporting/generation.py::_stress_traffic_lights). No Bank of
    # Ghana instrument requires a POST-STRESS capital adequacy ratio to meet the
    # minimum: the stress-testing and ICAAP directives are February 2026 exposure
    # drafts, and the minimum the Capital Requirements Directive does set binds
    # the ACTUAL ratio and is time-varying (CRD paragraph 71 with the paragraph 75
    # capital conservation buffer), so a single threshold would misstate it even
    # where one applied. The full quarterly path stays in metrics["stress_path"]
    # and the STRESS-PACK still tabulates it (_stress_ratio_evolution,
    # _stress_pro_forma, _stress_attribution); the advisory end-state figure with
    # a declared authority is the enterprise-stress engine's
    # ``stressed_car_end_pct`` (supervisory monitoring, not filed).
    #
    # Not persisting it fixes only what is sealed from here on. Runs sealed
    # BEFORE this change still carry the row, and a sealed run is append-only
    # evidence that must not be rewritten — so the pack refuses the verdict on
    # the READ path as well: ``_stress_traffic_lights`` prints a threshold and a
    # status only where the WS-A metric authority register declares the figure
    # filed under the engine that sealed it, and states in the artifact when it
    # does not.
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
        _validation_rows(ratios, params, stress, currency), start=1
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
    ratios: CapitalRatiosResult,
    params: CapitalParams,
    stress: CapitalStressResult | None,
    currency: str,
) -> tuple[tuple[str, bool, str, str], ...]:
    # CAR always applies; the Basel sub-tier + leverage minima apply only under
    # the bank (CRD) regime. Under SDI s.29 they are structurally excluded and
    # are omitted entirely rather than reported as passing against a 0% sentinel
    # (audit M3, docs/sdi.md §4.2).
    ratio_checks = [
        ("car_above_minimum", "CAR", ratios.car_pct, params.car_min_pct),
    ]
    if params.basel_applicable:
        ratio_checks += [
            ("cet1_above_minimum", "CET1 ratio", ratios.cet1_ratio_pct, params.cet1_min_pct),
            ("tier1_above_minimum", "Tier 1 ratio", ratios.tier1_ratio_pct, params.tier1_min_pct),
            (
                "leverage_above_minimum",
                "leverage ratio",
                ratios.leverage_ratio_pct,
                params.leverage_min_pct,
            ),
        ]
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
            f"of {ratios.general_provisions_amount} {currency} were capped at "
            f"{ratios.general_provisions_cap} {currency}."
        )
    else:
        gp_message = (
            f"The Tier 2 general provisions cap of {cap_pct}% of credit RWA did not bind: "
            f"provisions of {ratios.general_provisions_amount} {currency} are within the cap of "
            f"{ratios.general_provisions_cap} {currency}."
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


def _buffers_or_409(active: _ActiveCapitalParams, current_car: Decimal) -> CapitalBuffersRead:
    """The dashboard's floor block — ONE authority for every ratio's minimum (NEW-53).

    Carries the Basel sub-tier floors alongside the CAR ladder so a consumer
    never has to fall back to a stored run's ``threshold_min`` to find out what
    Tier 1, CET1 and leverage are judged against. A run's threshold is a record
    of what was applied when it ran; reading it as the current requirement made
    the Basel overview claim "this run carries no Tier 1 minimum" beside a green
    Tier 1 KPI and a passing Tier 1 validation, all on one screen, for any bank
    before its first official capital run.

    Read-only: the values come straight from the same ``active.thresholds`` dict
    :func:`_engine_params` hands the engine, so no financial output moves.
    """
    thresholds = active.thresholds
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
    # The Basel sub-tier floors exist only under the bank (CRD) regime — the same
    # gate ``_validation_rows`` applies. Under SDI s.29 they are structurally
    # excluded, so they are reported ABSENT rather than as a register row that
    # the s.29 engine never applies (``_SDI_STRUCTURAL_CAPITAL`` zeroes them).
    basel_applicable = active.institution_class != "sdi"

    def _sub_tier(code: str) -> Decimal | None:
        return thresholds.get(code) if basel_applicable else None

    return CapitalBuffersRead(
        car_min_pct=thresholds["car_min"],
        car_early_warning_pct=thresholds["car_early_warning"],
        car_early_warning_label=CAR_EARLY_WARNING_LABEL,
        car_critical_pct=thresholds["car_critical"],
        current_car_pct=current_car,
        headroom_pp=ratio_pct(current_car - thresholds["car_min"]),
        cet1_min_pct=_sub_tier("cet1_min"),
        tier1_min_pct=_sub_tier("tier1_min"),
        leverage_min_pct=_sub_tier("leverage_min"),
    )


# Dashboard trends show a trailing window, not the bank's full period history. With
# 10 years of monthly history (~120 periods) and few stored runs, recomputing every
# period inline on each load cost ~500 queries / ~20s; a trailing year is both fast
# and a readable sparkline. Tune here if a longer horizon is wanted.
_TREND_MAX_POINTS = 13


def _build_trend(
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    periods: list[BankReportingPeriod],
    *,
    batch: _CapitalDashboardBatch | None = None,
) -> list[CapitalTrendPointRead]:
    trend_periods = periods[-_TREND_MAX_POINTS:]
    batch = batch or _prefetch_dashboard_batch(db, ctx, bank, trend_periods)
    points: list[CapitalTrendPointRead] = []
    for period in trend_periods:
        run = batch.runs.get(period.id)
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
            _rwa, ratios, _params = _compute_inline_from_batch(db, ctx, bank, period, batch)
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


def _prefetch_dashboard_batch(
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    periods: list[BankReportingPeriod],
    *,
    extra_period: BankReportingPeriod | None = None,
) -> _CapitalDashboardBatch:
    candidates = [*periods[-_TREND_MAX_POINTS:]]
    if extra_period is not None and all(item.id != extra_period.id for item in candidates):
        candidates.append(extra_period)
    period_ids = [period.id for period in candidates]
    dates = [period.period_end for period in candidates]
    return _CapitalDashboardBatch(
        runs=regulatory_dashboard_batching.latest_succeeded_baseline_runs(
            db,
            organization_id=ctx.organization_id,
            bank=bank,
            module=MODULE_CAPITAL,
            scenario_code=BASELINE_SCENARIO,
            reporting_period_ids=period_ids,
        ),
        facts=regulatory_dashboard_batching.facts_by_period(
            db,
            organization_id=ctx.organization_id,
            bank=bank,
            reporting_period_ids=period_ids,
            fact_groups=_CAPITAL_FACT_GROUPS,
        ),
        weights=prefetch_active_params(
            db, ctx.organization_id, bank.jurisdiction_code, ParamRiskWeight, dates
        ),
        thresholds=prefetch_active_params(
            db, ctx.organization_id, bank.jurisdiction_code, ParamCapitalThreshold, dates
        ),
        crm=prefetch_active_params(
            db, ctx.organization_id, bank.jurisdiction_code, ParamCrmHaircut, dates
        ),
        ecl=prefetch_active_params(
            db, ctx.organization_id, bank.jurisdiction_code, ParamEclAssumption, dates
        ),
        governed=regulatory_parameters.PrefetchedParameterResolver.load(
            db, bank, as_of_dates=dates
        ),
    )


def _active_params_from_batch(
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    as_of: date,
    batch: _CapitalDashboardBatch,
) -> _ActiveCapitalParams:
    weight_rows = batch.weights.active_on(as_of)
    threshold_rows = batch.thresholds.active_on(as_of)
    crm_rows = batch.crm.active_on(as_of)
    ecl_rows = batch.ecl.active_on(as_of)
    crm_haircuts = dict(DEFAULT_CRM_HAIRCUTS)
    crm_haircuts.update({row.collateral_class: Decimal(str(row.haircut_pct)) for row in crm_rows})
    car_min_param = batch.governed.try_resolve("car_min", as_of=as_of)
    thresholds = batch.governed.clamp_overrides(
        {row.threshold_code: Decimal(str(row.value_pct)) for row in threshold_rows},
        as_of=as_of,
    ).values
    institution_class = batch.governed.scope.institution_class
    rwa_scope = (
        sdi_capital.resolve_rwa_scope(db, bank, as_of, resolver=batch.governed)
        if institution_class == "sdi"
        else None
    )
    return _ActiveCapitalParams(
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
        institution_class=institution_class,
        car_min_fallback=car_min_param.value if car_min_param is not None else None,
        rwa_scope=rwa_scope,
    )


def _compute_inline_from_batch(  # noqa: PLR0913 - explicit request scope plus optional reuse
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    period: BankReportingPeriod,
    batch: _CapitalDashboardBatch,
    *,
    active: _ActiveCapitalParams | None = None,
) -> tuple[RwaResult, CapitalRatiosResult, CapitalParams]:
    facts = batch.facts.get(period.id, [])
    if not facts:
        raise CapitalRunError(
            "financial_facts_missing",
            "The reporting period has no financial facts to analyze.",
            {"reporting_period_id": str(period.id)},
        )
    active = active or _active_params_from_batch(db, ctx, bank, period.period_end, batch)
    engine_params = _engine_params(active)
    engine_facts = tuple(_to_engine_fact(fact) for fact in facts)
    rwa = compute_rwa(engine_facts, engine_params)
    ratios = compute_capital_ratios(engine_facts, rwa, engine_params)
    return rwa, ratios, engine_params


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


def _compute_inline_or_409(  # noqa: PLR0913 - endpoint error boundary preserves named inputs
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    period: BankReportingPeriod,
    *,
    batch: _CapitalDashboardBatch | None = None,
    active: _ActiveCapitalParams | None = None,
) -> tuple[RwaResult, CapitalRatiosResult, CapitalParams]:
    try:
        return (
            _compute_inline_from_batch(db, ctx, bank, period, batch, active=active)
            if batch is not None
            else _compute_inline(db, ctx, bank, period)
        )
    except MissingParameterError as exc:
        raise ModuleDataUnavailable("missing_parameter", str(exc)) from exc
    except CapitalRunError as exc:
        raise ModuleDataUnavailable(exc.code, exc.message) from exc
    except CapitalComputationError as exc:
        raise ModuleDataUnavailable("calculation_error", str(exc)) from exc


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
    """Compute the baseline live view from current facts without a RegulatoryRun."""
    current = load_current_facts(db, ctx, bank, _CAPITAL_FACT_GROUPS)
    facts = current.facts
    active = _load_active_params(db, ctx, bank, current.source_as_of_date)
    # Branch by REGIME before any Basel arithmetic runs. ``compute_rwa`` below
    # resolves CRD exposure-class weights (RW0…RW150), which an SDI's control
    # plane does not carry — it carries the simplified s.29 bucket weights
    # (``risk_weight_cash``/``_interbank``/``_other_loans``/…). So for an SDI the
    # CRD engine raised ``No governed risk weight resolves for code 'RW150'`` and
    # the whole module failed before reaching the SDI findings branch further
    # down, which is additive and never got the chance to run.
    #
    # The metrics that path emits are CRD constructs besides — CET1, Tier 1 and
    # leverage ratios — which docs/sdi.md lists as EXCLUDE: "Basel CRD
    # constructs; SDIs are outside the CRD". Publishing them for an SDI would be
    # the bank scorecard problem again, one layer down.
    if active.institution_class == "sdi":
        return _sdi_compute_live(db, ctx, bank, period, current, active, facts)
    params = _engine_params(active)
    engine_facts = tuple(_to_engine_fact(fact) for fact in facts)
    rwa = compute_rwa(engine_facts, params)
    ratios = compute_capital_ratios(engine_facts, rwa, params)
    snapshot = current_snapshot(
        _build_snapshot(bank, period, BASELINE_SCENARIO, facts, active, {}),
        current.source_as_of_date,
    )
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
    findings = findings_from_validations(
        _validation_rows(ratios, params, None, base_currency(bank)), status
    )
    # SDI simplified-capital checks reconciled into live findings + the module
    # status (QA audit 2026-08-20 P1-6): a paid-up / statutory-reserve shortfall now
    # surfaces in Alerts and governance signals, not only on the s.29 diagnostics
    # page. Bank tenants are untouched (the branch is class-gated).
    if active.institution_class == "sdi":
        sdi_findings, sdi_status = _sdi_capital_live_findings(
            db, ctx, bank, current.source_as_of_date
        )
        findings = findings + sdi_findings
        status = worst_status(status, sdi_status)
    return LiveModuleResult(
        metrics=metrics,
        status=status,
        input_hash=_snapshot_hash(snapshot),
        findings=findings,
        source_as_of_date=current.source_as_of_date,
    )


def _sdi_compute_live(  # noqa: PLR0913 - the live context, spelled out
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    period: BankReportingPeriod,
    current: Any,
    active: Any,
    facts: Any,
) -> LiveModuleResult:
    """The s.29 live capital view: NOF ÷ simplified-bucket RWA, no CRD ratios.

    Uses the same authority the official SDI run uses
    (``sdi_capital.compute_sdi_capital_summary``), so the live tile and the
    filing figure cannot disagree about an institution's capital adequacy.

    Emits no CET1, Tier 1 or leverage ratio: an SDI has none, and a zero would
    read as a breach rather than an absence.
    """
    as_of = current.source_as_of_date
    summary = sdi_capital.compute_sdi_capital_summary(db, ctx, bank, as_of)
    metrics: dict[str, str] = {
        "car_pct": "" if summary.car_pct is None else str(summary.car_pct),
        "car_min_pct": str(summary.car_min_pct),
        "total_rwa_ghs": str(summary.total_rwa_ghs),
        "net_own_funds_ghs": str(summary.net_own_funds_ghs),
        "capital_regime": "s29",
        # Provenance the s.29 page already shows, carried onto the live tile so
        # an operator sees WHY a figure is pending without leaving Markets.
        "rwa_composition_source": summary.composition_source,
        "bucket_map_source": summary.bucket_map_source,
    }
    if summary.pending_parameters:
        metrics["pending_parameters"] = ", ".join(summary.pending_parameters)
    findings, status = _sdi_capital_live_findings(db, ctx, bank, as_of)
    if not summary.computable:
        # Not computable is ``na``, never green: an unresolved governed input is
        # an absence of a verdict, not a passing one.
        status = worst_status(status, "na")
    else:
        status = worst_status(status, summary.status)
    return LiveModuleResult(
        metrics=metrics,
        status=status,
        input_hash=_snapshot_hash(
            current_snapshot(
                _build_snapshot(bank, period, BASELINE_SCENARIO, facts, active, {}),
                as_of,
            )
        ),
        findings=findings,
        source_as_of_date=as_of,
    )


def _sdi_capital_live_findings(
    db: Session, ctx: TenantContext, bank: Bank, as_of: date
) -> tuple[tuple[LiveFindingSpec, ...], str]:
    """SDI simplified-capital checks (paid-up + statutory reserve) as live findings.

    A hard licensing floor (paid-up capital below the statutory minimum) is a
    ``critical`` finding and drives the capital module red; a reserve-fund shortfall
    is a ``medium`` finding driving it amber. A not-computable check (missing data)
    raises no alert — the s.29 diagnostics page surfaces it, but a missing input is
    not a breach. Values come from ``sdi_capital_checks`` (control-plane thresholds
    with provenance), so the alert and the s.29 page share one source of truth."""
    checks = (
        (sdi_capital_checks.check_paid_up_capital(db, ctx, bank, as_of), "critical", "red"),
        (sdi_capital_checks.check_statutory_reserve_fund(db, ctx, bank, as_of), "medium", "amber"),
    )
    findings: list[LiveFindingSpec] = []
    status = "green"
    # Audit 2026-08-22 D-19: the live view MAY compute on an undetermined RWA
    # scope — a management view of a provisional ratio is legitimate — but it must
    # say so where the ratio is read, not only on the s.29 diagnostics page. The
    # official mint refuses the same condition outright
    # (``sdi_capital.assert_official_rwa_scope_governed``).
    scope = sdi_capital.resolve_rwa_scope(db, bank, as_of)
    if not scope.filable:
        if not scope.confirmed:
            scope_message = (
                "This capital adequacy ratio is provisional: which risk classes its "
                "risk-weighted assets charge for has not been approved for this "
                "institution, so the platform is applying its documented default of "
                "credit risk only. No market-risk or operational-risk charge is assumed."
            )
        else:
            scope_message = (
                "This capital adequacy ratio is provisional: the capital-adequacy scope "
                "approved for this institution is still pending confirmation against a "
                "published regulatory instrument."
            )
        findings.append(
            LiveFindingSpec(
                rule_id="sdi_capital.rwa_scope_provisional",
                severity="medium",
                message=(
                    scope_message + " The ratio may be used for management purposes; an "
                    "official run cannot be produced until the scope is approved and "
                    "confirmed in the regulatory-parameter control plane."
                ),
                metric="rwa_scope",
            )
        )
        status = worst_status(status, "amber")
    for result, severity, breach_status in checks:
        if result.compliant is False:
            findings.append(
                LiveFindingSpec(
                    rule_id=f"sdi_capital.{result.check}",
                    severity=severity,
                    message=result.detail,
                    metric=result.check,
                )
            )
            status = worst_status(status, breach_status)
    return tuple(findings), status


def _baseline_run_or_409(
    db: Session,
    ctx: TenantContext,
    bank_id: str,
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


def _tier2_row_description(
    line: CapitalLineRead, cap_pct: Decimal, credit_rwa: Decimal, currency: str
) -> str:
    if line.line_code != _GP_LINE_CODE:
        return line.description
    cap_amount = money(credit_rwa * cap_pct / _HUNDRED)
    exposure = line.exposure_amount if line.exposure_amount is not None else line.weighted_amount
    bound = line.weighted_amount < exposure
    return (
        f"General Provisions (Tier 2 cap {_pct_text(cap_pct)}% of credit RWA "
        f"= {cap_amount} {currency}; " + ("cap bound" if bound else "cap not binding") + ")"
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


def _to_engine_fact(fact: FinancialFactRow) -> CapitalFact:
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
    crm_rows = get_active_params(
        db, ctx.organization_id, bank.jurisdiction_code, ParamCrmHaircut, as_of
    )
    ecl_rows = get_active_params(
        db, ctx.organization_id, bank.jurisdiction_code, ParamEclAssumption, as_of
    )
    crm_haircuts = dict(DEFAULT_CRM_HAIRCUTS)
    crm_haircuts.update({row.collateral_class: Decimal(str(row.haircut_pct)) for row in crm_rows})
    # The institution class selects the capital regime; the control-plane CAR
    # floor is the fallback used only when the tenant has no board threshold row
    # (docs/sdi.md §4.2, §7 Phase E). ``try_resolve`` is None for a jurisdiction
    # with no seeded default — the bank path never depends on it.
    car_min_param = regulatory_parameters.try_resolve(db, bank, "car_min", as_of=as_of)
    # Tighten-only guard, GENERALISED (QA audit 2026-08-20 P1-5): every governed
    # code in this board register is clamped against its control-plane value in one
    # pass, not just ``car_min``. A board minimum weaker than the regulatory floor
    # is raised to it; a stricter one stands. Codes with no seeded control-plane row
    # pass through untouched — a floor is never invented to clamp against.
    thresholds = regulatory_parameters.clamp_overrides(
        db,
        bank,
        {row.threshold_code: Decimal(str(row.value_pct)) for row in threshold_rows},
        as_of=as_of,
    ).values
    institution_class = _resolve_institution_class(db, bank)
    # An SDI's RWA scope is governed data, resolved HERE — before the run row is
    # created — so a scope that cannot be established refuses (409) instead of
    # minting a filing run against an incomplete total. A bank never resolves it:
    # the BoG CRD path has its own scope and is untouched.
    rwa_scope = (
        sdi_capital.resolve_rwa_scope(db, bank, as_of) if institution_class == "sdi" else None
    )
    return _ActiveCapitalParams(
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
        institution_class=institution_class,
        car_min_fallback=car_min_param.value if car_min_param is not None else None,
        rwa_scope=rwa_scope,
    )


# SDI simplified s.29 capital (docs/sdi.md §4.2): there are no Basel sub-tier
# (CET1/Tier1) minimums and no leverage floor, and the CAR RAG collapses to the
# single s.29 floor. These are STRUCTURAL s.29 settings that configure the SHARED
# engine — they are NOT tunable regulatory values (the CAR floor and the risk
# weights are, and come from the control plane / the tenant register), and they are
# NOT a scope declaration.
#
# WHICH risk classes s.29 risk-weighted assets charge for is governed data with a
# single authority, ``sdi_capital.resolve_rwa_scope``, consumed by EVERY SDI capital
# path: this module's live view and filing run, and the solvency stress projection
# in ``enterprise_stress._sdi_capital_params``. This dict used to answer that
# question a second time, by zeroing ``fx_charge_pct`` and ``bia_alpha_pct``; a
# governed row that turned a charge on would then have moved the live CAR and left
# the official CAR behind.
#
# The two Basel MEASUREMENT rates below are zero because no s.29 measurement uses
# them: a governed composition may only name ``bucket_weighted_exposure`` (credit)
# or ``pct_of_credit_rwa``, never the BIA gross-income alpha or the FX open-position
# charge — ``resolve_rwa_scope`` refuses anything else. A class the composition
# brings into scope therefore reaches the engine through
# ``CapitalParams.rwa_pct_of_credit_rwa``, never through these two.
_SDI_STRUCTURAL_CAPITAL = {
    "bia_alpha_pct": _ZERO,
    "fx_charge_pct": _ZERO,
    "rwa_multiplier": Decimal("1250"),
    "tier2_gp_cap_pct_credit_rwa": Decimal("1.25"),
    "cet1_min": _ZERO,
    "tier1_min": _ZERO,
    "leverage_min": _ZERO,
}


def _engine_params(active: _ActiveCapitalParams) -> CapitalParams:
    """Build the engine params for the tenant's capital regime. A bank runs the
    full BoG CRD set (unchanged — byte-identical); an SDI runs the simplified
    s.29 set (only the CAR floor is a required regulatory value)."""
    if active.institution_class == "sdi":
        return _sdi_engine_params(active)
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
        crm_haircuts=active.crm_haircuts,
    )


def _sdi_engine_params(active: _ActiveCapitalParams) -> CapitalParams:
    """Simplified s.29 capital params (docs/sdi.md §4.2). The ONLY required
    regulatory value is the CAR floor — the tenant's board row if set, else the
    control-plane class default (10%); the run fails loud if neither exists. The
    RAG has a single floor (early-warning = critical = the floor, no CCB band).
    Risk weights come from the tenant's ParamRiskWeight register exactly as for a
    bank — an SDI configures them or the engine fails loud on a loan (never a
    made-up weight). Tier/leverage take the s.29 structural settings above.

    WHICH risk classes the total charges for comes from the governed scope
    (``sdi_capital.resolve_rwa_scope``, resolved in :func:`_load_active_params`) —
    the same object the live s.29 view and the solvency stress projection consume,
    so no two of them can charge for different things. A charged class arrives as a
    percentage of credit RWA; nothing is defaulted here and no percentage is
    invented."""
    car_min = active.thresholds.get("car_min", active.car_min_fallback)
    if car_min is None:
        raise CapitalRunError(
            "missing_parameter",
            "The SDI CAR floor (car_min) is configured neither on the institution's "
            "board register nor in the regulatory-parameter control plane.",
            {"threshold_codes": ["car_min"]},
        )
    # Audit 2026-08-22 D-19: this read used to substitute
    # ``sdi_capital.default_rwa_scope()`` when the resolved scope was absent — an
    # unlabelled fallback to the code default on the path that mints filing
    # evidence. ``_load_active_params`` resolves the scope for every SDI, so a
    # missing one means the class check and the params build disagree; that is a
    # defect, not a default.
    scope = active.rwa_scope
    if scope is None:
        raise CapitalRunError(
            "policy_unresolved",
            "The governed capital-adequacy scope for this institution was not resolved, "
            "so which risk classes risk-weighted assets charge for is unknown. No scope "
            "is assumed. Resolve "
            f"{sdi_capital.COMPOSITION_PARAM} in the regulatory-parameter control plane.",
            {"param_codes": [sdi_capital.COMPOSITION_PARAM]},
        )
    if not scope.credit_in_scope:
        # The filing engine measures credit risk from the risk-weighted book; there
        # is no way to file a total that leaves credit risk out, and a total that
        # says it does is not a total.
        raise CapitalRunError(
            "policy_unresolved",
            "The approved capital-adequacy scope for this institution does not "
            "include credit risk measured from the asset book, which is the only "
            "basis on which risk-weighted assets can be filed. Correct "
            f"{sdi_capital.COMPOSITION_PARAM} in the regulatory-parameter control "
            "plane.",
            {"param_codes": [sdi_capital.COMPOSITION_PARAM]},
        )
    s = _SDI_STRUCTURAL_CAPITAL
    return CapitalParams(
        risk_weights=active.risk_weights,
        bia_alpha_pct=s["bia_alpha_pct"],
        fx_charge_pct=s["fx_charge_pct"],
        rwa_multiplier_pct=s["rwa_multiplier"],
        tier2_gp_cap_pct_credit_rwa=s["tier2_gp_cap_pct_credit_rwa"],
        cet1_min_pct=s["cet1_min"],
        tier1_min_pct=s["tier1_min"],
        car_min_pct=car_min,
        leverage_min_pct=s["leverage_min"],
        car_early_warning_pct=car_min,
        car_critical_pct=car_min,
        crm_haircuts=active.crm_haircuts,
        # s.29: the Basel sub-tier / leverage minima do not apply and must not be
        # surfaced as passing compliance rules against a 0% sentinel (audit M3).
        basel_applicable=False,
        rwa_pct_of_credit_rwa=dict(scope.pct_of_credit_rwa),
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
    facts: Sequence[FinancialFactRow],
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
        "parameters": _snapshot_parameters(active),
        "shocks": _stringified(shocks),
    }


def _snapshot_parameters(active: _ActiveCapitalParams) -> dict[str, Any]:
    """ECL/CRM blocks join the snapshot only when configured/consumed, so
    pre-existing books hash exactly as before (value-based discipline)."""
    parameters: dict[str, Any] = {
        "risk_weights_pct": _stringified(active.risk_weights),
        "thresholds_pct": _stringified(active.thresholds),
    }
    configured_crm = {
        key: value
        for key, value in active.crm_haircuts.items()
        if DEFAULT_CRM_HAIRCUTS.get(key) != value
    }
    if configured_crm:
        parameters["crm_haircuts_pct"] = _stringified(configured_crm)
    if active.rwa_scope is not None and active.rwa_scope.pct_of_credit_rwa:
        # Value-based like every other block: it joins the hash only when it CHANGES
        # risk-weighted assets, so a credit-only scope (governed or default) hashes
        # exactly as the pre-composition books did.
        parameters["sdi_rwa_charges_pct_of_credit_rwa"] = _stringified(
            dict(active.rwa_scope.pct_of_credit_rwa)
        )
    if active.rwa_scope is not None:
        # Audit 2026-08-22 D-19: the run recorded WHAT the scope charged for (above,
        # and only when it moved a number) but never WHETHER anyone determined it.
        # A reader of a sealed run could not tell an approved credit-only scope from
        # the platform's placeholder. This block is SDI-only — ``rwa_scope`` is None
        # for every bank — so the BoG CRD input hash is byte-identical.
        parameters["sdi_rwa_composition"] = {
            "source": active.rwa_scope.source,
            "confirmation_status": active.rwa_scope.confirmation_status,
            "composition": {
                risk_class: active.rwa_scope.composition[risk_class]
                for risk_class in sorted(active.rwa_scope.composition)
            },
        }
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


def capital_breach_multiplier(
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    period: BankReportingPeriod,
    scenario_code: str = "severe",
) -> dict[str, Any]:
    """Reverse stress (Phase 2 item 4): the smallest severity multiplier k at
    which the four-quarter CET1 path breaches the CET1 minimum.

    k scales the named capital scenario's quarterly credit losses and RWA
    growth linearly and moves the FX RWA multiplier from 1 toward its shocked
    value; quarterly income is NOT scaled (income does not rise with
    severity). Deterministic bisection to 0.05 precision over k in (0, 5].
    """
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
    shocks = _load_shocks(db, ctx, bank, scenario_code, period.period_end)
    if not shocks:
        raise CapitalRunError(
            "missing_parameter",
            f"No capital stress shocks are configured for scenario '{scenario_code}'.",
            {"scenario_code": scenario_code},
        )
    one = Decimal("1")

    def scaled_shocks(k: Decimal) -> dict[str, Decimal]:
        return {
            "quarterly_rwa_growth_pct": shocks["quarterly_rwa_growth_pct"] * k,
            "quarterly_income_m": shocks["quarterly_income_m"],
            "quarterly_credit_loss_m": shocks["quarterly_credit_loss_m"] * k,
            "fx_rwa_multiplier": one + (shocks["fx_rwa_multiplier"] - one) * k,
        }

    def worst_cet1_at(k: Decimal) -> Decimal:
        result = run_capital_stress(scenario_code, engine_facts, engine_params, scaled_shocks(k))
        return min(quarter.cet1_ratio for quarter in result.path)

    minimum = engine_params.cet1_min_pct
    k_max = Decimal("5")
    precision = Decimal("0.05")
    baseline_cet1 = worst_cet1_at(Decimal("0"))
    if worst_cet1_at(k_max) >= minimum:
        return {
            "breached": False,
            "scenario_code": scenario_code,
            "cet1_min_pct": str(minimum),
            "baseline_worst_cet1_pct": str(baseline_cet1),
            "worst_cet1_at_k_max_pct": str(worst_cet1_at(k_max)),
            "k_max": str(k_max),
        }
    low, high = Decimal("0"), k_max
    while high - low > precision:
        mid = (low + high) / 2
        if worst_cet1_at(mid) < minimum:
            high = mid
        else:
            low = mid
    frontier = high.quantize(precision)
    return {
        "breached": True,
        "scenario_code": scenario_code,
        "cet1_min_pct": str(minimum),
        "baseline_worst_cet1_pct": str(baseline_cet1),
        "breach_multiplier": str(frontier),
        "worst_cet1_at_breach_pct": str(worst_cet1_at(frontier)),
    }
