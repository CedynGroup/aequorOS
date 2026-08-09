"""Scenario analysis execution — the desk's what-if engine.

Runs any mix of system and custom scenarios (with optional ad-hoc overrides)
through the SAME per-module arithmetic the official runs use
(`compute_scenario_analysis`), and returns side-by-side results **without
writing a RegulatoryRun** — transient by default, snapshotted only when the
analyst explicitly saves. Per-scenario failures return as data so one bad
scenario never sinks the comparison.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.db.base import utc_now
from app.domain.capital.engine import (
    CapitalComputationError,
)
from app.domain.capital.engine import (
    MissingParameterError as CapitalMissingParameter,
)
from app.domain.capital.engine import (
    UnsupportedShockError as CapitalUnsupportedShock,
)
from app.domain.ftp.engine import (
    FtpComputationError,
)
from app.domain.ftp.engine import (
    MissingParameterError as FtpMissingParameter,
)
from app.domain.fx.engine import (
    FxComputationError,
)
from app.domain.fx.engine import (
    MissingParameterError as FxMissingParameter,
)
from app.domain.irr.engine import (
    IrrComputationError,
)
from app.domain.irr.engine import (
    MissingParameterError as IrrMissingParameter,
)
from app.domain.irr.engine import (
    UnsupportedShockError as IrrUnsupportedShock,
)
from app.domain.liquidity.engine import (
    LiquidityComputationError,
)
from app.domain.liquidity.engine import (
    UnsupportedShockError as LiquidityUnsupportedShock,
)
from app.models import Bank, BankReportingPeriod, SavedScenarioAnalysis, StressScenario
from app.schemas.scenario_workbench import (
    AnalysisRunCreate,
    AnalysisRunRead,
    SavedAnalysisCreate,
    SavedAnalysisListRead,
    SavedAnalysisRead,
    SavedAnalysisSummaryRead,
    ScenarioRefIn,
    ScenarioResultRead,
    WorkbenchModule,
)
from app.services import (
    regulatory_capital,
    regulatory_ftp,
    regulatory_fx,
    regulatory_irr,
    regulatory_liquidity,
)
from app.services.audit import record_event
from app.services.scenario_catalog import SYSTEM_SCENARIOS, system_shocks, validate_shocks
from app.services.stress_scenarios import _get_bank_or_404  # noqa: PLC2701 - shared guard

_ENGINE_VERSIONS = {
    "liquidity": regulatory_liquidity.ENGINE_VERSION,
    "capital": regulatory_capital.ENGINE_VERSION,
    "irr": regulatory_irr.ENGINE_VERSION,
    "fx": regulatory_fx.ENGINE_VERSION,
    "ftp": regulatory_ftp.ENGINE_VERSION,
}

# Domain failures that become per-scenario failed entries rather than HTTP
# errors — the same failures-as-data posture as the run lifecycle.
_DOMAIN_ERRORS: tuple[type[Exception], ...] = (
    regulatory_liquidity.LiquidityRunError,
    regulatory_capital.CapitalRunError,
    regulatory_irr.IrrRunError,
    regulatory_fx.FxRunError,
    regulatory_ftp.FtpRunError,
    LiquidityComputationError,
    LiquidityUnsupportedShock,
    CapitalComputationError,
    CapitalMissingParameter,
    CapitalUnsupportedShock,
    IrrComputationError,
    IrrMissingParameter,
    IrrUnsupportedShock,
    FxComputationError,
    FxMissingParameter,
    FtpComputationError,
    FtpMissingParameter,
)


@dataclass(frozen=True)
class _ResolvedRef:
    kind: str
    code: str
    scenario_id: UUID | None
    label: str
    shocks: dict[str, Decimal]
    overrides: dict[str, Decimal]


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


def _resolve_ref(  # noqa: PLR0913 - resolution needs the full scoping
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    module: WorkbenchModule,
    period: BankReportingPeriod,
    ref: ScenarioRefIn,
) -> _ResolvedRef:
    overrides = dict(ref.overrides or {})
    if overrides:
        validate_shocks(module, overrides)
    if ref.kind == "system":
        definitions = {entry.code: entry for entry in SYSTEM_SCENARIOS[module]}
        definition = definitions.get(ref.code or "")
        if definition is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Unknown system scenario '{ref.code}' for {module}.",
            )
        shocks = system_shocks(db, ctx, bank, module, definition.code, period.period_end)
        return _ResolvedRef(
            kind="system",
            code=definition.code,
            scenario_id=None,
            label=definition.label,
            shocks={**shocks, **overrides},
            overrides=overrides,
        )
    scenario = db.scalar(
        select(StressScenario).where(
            StressScenario.id == ref.scenario_id,
            StressScenario.organization_id == ctx.organization_id,
            StressScenario.bank_id == bank.id,
            StressScenario.module == module,
            StressScenario.is_archived.is_(False),
        )
    )
    if scenario is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scenario not found.")
    shocks = {key: Decimal(str(value)) for key, value in scenario.shocks.items()}
    return _ResolvedRef(
        kind="custom",
        code=scenario.code,
        scenario_id=scenario.id,
        label=scenario.name,
        shocks={**shocks, **overrides},
        overrides=overrides,
    )


def _stringified(values: dict[str, Decimal]) -> dict[str, str]:
    return {key: str(value) for key, value in sorted(values.items())}


# --- per-module result mappers ----------------------------------------------


def _liquidity_result(analysis: regulatory_liquidity.LiquidityScenarioAnalysis):
    gaps = analysis.currency_gaps
    metrics = {
        "lcr_pct": str(analysis.lcr.lcr_pct),
        "nsfr_pct": str(analysis.nsfr.nsfr_pct),
        "hqla_total_ghs": str(analysis.lcr.hqla_total),
        "net_outflows_30d_ghs": str(analysis.lcr.net_outflows_total),
        "fx_funding_gap_ghs": str(gaps.fx_funding_gap),
        "fx_share_of_liabilities_pct": str(gaps.fx_share_of_liabilities_pct),
    }
    statuses = {"lcr_pct": analysis.lcr.status, "nsfr_pct": analysis.nsfr.status}
    return metrics, statuses


def _capital_result(analysis: regulatory_capital.CapitalScenarioAnalysis):
    ratios = analysis.ratios
    metrics = {
        "car_pct": str(ratios.car_pct),
        "tier1_ratio_pct": str(ratios.tier1_ratio_pct),
        "cet1_ratio_pct": str(ratios.cet1_ratio_pct),
        "leverage_ratio_pct": str(ratios.leverage_ratio_pct),
        "total_rwa_ghs": str(analysis.rwa.total_rwa),
        "total_capital_ghs": str(ratios.total_capital),
    }
    if analysis.stress is not None:
        worst = min(quarter.cet1_ratio for quarter in analysis.stress.path)
        metrics["worst_quarter_cet1_pct"] = str(worst)
    statuses = {
        "car_pct": ratios.car_status,
        "tier1_ratio_pct": ratios.tier1_status,
        "cet1_ratio_pct": ratios.cet1_status,
        "leverage_ratio_pct": ratios.leverage_status,
    }
    return metrics, statuses


def _irr_result(analysis: regulatory_irr.IrrScenarioAnalysis):
    metrics = {
        "base_eve_ghs": str(analysis.base_eve),
        "shifted_eve_ghs": str(analysis.shifted_eve),
        "delta_eve_ghs": str(analysis.delta_eve),
        "delta_eve_pct_tier1": str(analysis.delta_eve_pct_tier1),
        "eve_limit_pct": str(analysis.eve_limit_pct),
    }
    if analysis.ear is not None:
        metrics["ear_ghs"] = str(analysis.ear)
    statuses = {"delta_eve_pct_tier1": analysis.status}
    return metrics, statuses


def _fx_result(analysis: regulatory_fx.FxScenarioAnalysis):
    nop = analysis.nop
    metrics = {
        "nop_pct_tier1": str(nop.nop_pct_tier1),
        "overall_nop_ghs": str(nop.overall_nop),
        "single_ccy_max_pct": str(nop.single_ccy_max_pct),
        "aggregate_limit_pct": str(analysis.aggregate_limit_pct),
    }
    statuses = {
        "nop_pct_tier1": "green" if nop.within_aggregate_limit else "red",
        "single_ccy_max_pct": "green" if nop.within_single_limit else "red",
    }
    if analysis.scenario is not None:
        metrics["stressed_nop_pct_tier1"] = str(analysis.scenario.nop_pct_tier1)
        metrics["stressed_nop_ghs"] = str(analysis.scenario.nop_ghs)
        statuses["stressed_nop_pct_tier1"] = (
            "green" if analysis.scenario.within_aggregate_limit else "red"
        )
    return metrics, statuses


def _ftp_result(analysis) -> tuple[dict[str, str], dict[str, str]]:
    products = analysis.products
    metrics = {
        "portfolio_nim_pct": str(products.portfolio_nim_pct),
        "total_contribution_ghs": str(products.total_contribution_ghs),
        "products_below_min_margin": str(products.products_below_min_margin),
        "curve_shift_pct": str(analysis.curve_shift_pct),
        "nmd_core_pct": str(analysis.nmd.core_pct),
    }
    statuses = {
        "products_below_min_margin": (
            "green" if products.products_below_min_margin == 0 else "amber"
        )
    }
    return metrics, statuses


def _compute_one(  # noqa: PLR0913 - dispatch needs the full scoping
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    period: BankReportingPeriod,
    module: WorkbenchModule,
    resolved: _ResolvedRef,
) -> ScenarioResultRead:
    try:
        if module == "liquidity":
            metrics, statuses = _liquidity_result(
                regulatory_liquidity.compute_scenario_analysis(
                    db, ctx, bank, period, resolved.shocks, resolved.code
                )
            )
        elif module == "capital":
            metrics, statuses = _capital_result(
                regulatory_capital.compute_scenario_analysis(
                    db, ctx, bank, period, resolved.shocks, resolved.code
                )
            )
        elif module == "irr":
            metrics, statuses = _irr_result(
                regulatory_irr.compute_scenario_analysis(
                    db, ctx, bank, period, resolved.shocks, resolved.code
                )
            )
        elif module == "fx":
            metrics, statuses = _fx_result(
                regulatory_fx.compute_scenario_analysis(
                    db, ctx, bank, period, resolved.shocks, resolved.code
                )
            )
        else:
            metrics, statuses = _ftp_result(
                regulatory_ftp.compute_scenario_analysis(
                    db, ctx, bank, period, resolved.shocks, resolved.code
                )
            )
    except _DOMAIN_ERRORS as exc:  # noqa: BLE001 - failures-as-data, run-lifecycle posture
        code = getattr(exc, "code", None) or "calculation_error"
        message = getattr(exc, "message", None) or str(exc)
        return ScenarioResultRead(
            kind=resolved.kind,  # pyright: ignore[reportArgumentType]
            code=resolved.code,
            scenario_id=resolved.scenario_id,
            label=resolved.label,
            shocks_applied=_stringified(resolved.shocks),
            status="failed",
            error_code=str(code),
            error_message=str(message),
        )
    return ScenarioResultRead(
        kind=resolved.kind,  # pyright: ignore[reportArgumentType]
        code=resolved.code,
        scenario_id=resolved.scenario_id,
        label=resolved.label,
        shocks_applied=_stringified(resolved.shocks),
        status="succeeded",
        metrics=metrics,
        metric_statuses={key: str(value) for key, value in statuses.items()},
    )


def run_analysis(
    db: Session,
    ctx: TenantContext,
    bank_id: str,
    module: WorkbenchModule,
    payload: AnalysisRunCreate,
) -> AnalysisRunRead:
    """Compute the requested scenarios side by side. Writes nothing."""
    bank = _get_bank_or_404(db, ctx, bank_id)
    period = _get_period_or_404(db, ctx, bank, payload.reporting_period_id)
    resolved = [_resolve_ref(db, ctx, bank, module, period, ref) for ref in payload.scenarios]
    results = [_compute_one(db, ctx, bank, period, module, entry) for entry in resolved]
    return AnalysisRunRead(
        bank_id=bank.id,
        module=module,
        reporting_period_id=period.id,
        period_label=period.label,
        as_of_date=period.period_end,
        engine_version=_ENGINE_VERSIONS[module],
        computed_at=utc_now(),
        results=results,
    )


# --- saved analyses ----------------------------------------------------------


def _summary(row: SavedScenarioAnalysis) -> SavedAnalysisSummaryRead:
    return SavedAnalysisSummaryRead(
        id=row.id,
        module=row.module,  # pyright: ignore[reportArgumentType]
        reporting_period_id=row.reporting_period_id,
        name=row.name,
        engine_version=row.engine_version,
        scenario_count=len(row.scenarios),
        created_by=row.created_by,
        created_at=row.created_at,
    )


def _full(row: SavedScenarioAnalysis) -> SavedAnalysisRead:
    return SavedAnalysisRead(
        id=row.id,
        module=row.module,  # pyright: ignore[reportArgumentType]
        reporting_period_id=row.reporting_period_id,
        name=row.name,
        engine_version=row.engine_version,
        scenarios=row.scenarios,
        results=[ScenarioResultRead.model_validate(entry) for entry in row.results],
        created_by=row.created_by,
        created_at=row.created_at,
    )


def save_analysis(
    db: Session,
    ctx: TenantContext,
    bank_id: str,
    module: WorkbenchModule,
    payload: SavedAnalysisCreate,
) -> SavedAnalysisRead:
    """Recompute server-side and snapshot — clients never supply numbers."""
    bank = _get_bank_or_404(db, ctx, bank_id)
    period = _get_period_or_404(db, ctx, bank, payload.reporting_period_id)
    resolved = [_resolve_ref(db, ctx, bank, module, period, ref) for ref in payload.scenarios]
    results = [_compute_one(db, ctx, bank, period, module, entry) for entry in resolved]
    row = SavedScenarioAnalysis(
        organization_id=ctx.organization_id,
        bank_id=bank.id,
        module=module,
        reporting_period_id=period.id,
        name=payload.name,
        engine_version=_ENGINE_VERSIONS[module],
        scenarios=[
            {
                "kind": entry.kind,
                "code": entry.code,
                "scenario_id": str(entry.scenario_id) if entry.scenario_id else None,
                "label": entry.label,
                "shocks": _stringified(entry.shocks),
                "overrides": _stringified(entry.overrides),
            }
            for entry in resolved
        ],
        results=[result.model_dump(mode="json") for result in results],
        created_by=ctx.actor_user_id,
    )
    db.add(row)
    db.flush()
    record_event(
        db,
        ctx,
        event_type="scenario_analysis.saved",
        entity_type="scenario_analysis",
        entity_id=row.id,
        details={"module": module, "name": payload.name, "scenarios": len(resolved)},
    )
    db.commit()
    return _full(row)


def list_analyses(  # noqa: PLR0913 - one read carries its full scoping
    db: Session,
    ctx: TenantContext,
    bank_id: str,
    module: WorkbenchModule,
    limit: int = 25,
    offset: int = 0,
) -> SavedAnalysisListRead:
    bank = _get_bank_or_404(db, ctx, bank_id)
    conditions = [
        SavedScenarioAnalysis.organization_id == ctx.organization_id,
        SavedScenarioAnalysis.bank_id == bank.id,
        SavedScenarioAnalysis.module == module,
    ]
    total = (
        db.scalar(select(func.count()).select_from(SavedScenarioAnalysis).where(*conditions)) or 0
    )
    rows = db.scalars(
        select(SavedScenarioAnalysis)
        .where(*conditions)
        .order_by(SavedScenarioAnalysis.created_at.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return SavedAnalysisListRead(
        analyses=[_summary(row) for row in rows], total=int(total), limit=limit, offset=offset
    )


def get_analysis(
    db: Session, ctx: TenantContext, bank_id: str, module: WorkbenchModule, analysis_id: UUID
) -> SavedAnalysisRead:
    bank = _get_bank_or_404(db, ctx, bank_id)
    row = db.scalar(
        select(SavedScenarioAnalysis).where(
            SavedScenarioAnalysis.id == analysis_id,
            SavedScenarioAnalysis.organization_id == ctx.organization_id,
            SavedScenarioAnalysis.bank_id == bank.id,
            SavedScenarioAnalysis.module == module,
        )
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Analysis not found.")
    return _full(row)


def delete_analysis(
    db: Session, ctx: TenantContext, bank_id: str, module: WorkbenchModule, analysis_id: UUID
) -> None:
    bank = _get_bank_or_404(db, ctx, bank_id)
    row = db.scalar(
        select(SavedScenarioAnalysis).where(
            SavedScenarioAnalysis.id == analysis_id,
            SavedScenarioAnalysis.organization_id == ctx.organization_id,
            SavedScenarioAnalysis.bank_id == bank.id,
            SavedScenarioAnalysis.module == module,
        )
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Analysis not found.")
    record_event(
        db,
        ctx,
        event_type="scenario_analysis.deleted",
        entity_type="scenario_analysis",
        entity_id=row.id,
        details={"module": module, "name": row.name},
    )
    db.delete(row)
    db.commit()
