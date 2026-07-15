"""Data Engine activation: derive module facts, then recompute all six modules.

The activation is the bridge between Slice A (canonical ingestion) and the
regulatory modules: it derives the ``BankFinancialFact`` set for the requested
as-of date (see ``app.services.fact_derivation``) and then, unless asked not
to, runs every module's full scenario batch for the new period plus a base
balance-sheet forecast.

Failure semantics are deliberately partial: the derivation must succeed (a 409
is raised when the canonical state cannot support one), but each module
recomputation failure is captured as data on the response — an uploaded book
that supports five of six modules activates five dashboards and says why the
sixth failed. Re-activating the same as-of date rebuilds the facts (the
activation owns its period) and creates NEW immutable runs; prior run history
is never touched.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import AuditEvent, Bank
from app.schemas.data_activation import (
    ActivationGroupRead,
    ActivationRunRead,
    DataActivationCreate,
    DataActivationListRead,
    DataActivationRead,
    DataActivationSummaryRead,
)
from app.schemas.forecasting import ForecastRunCreate
from app.schemas.regulatory_capital import CapitalScenarioBatchCreate
from app.schemas.regulatory_ftp import FtpScenarioBatchCreate
from app.schemas.regulatory_fx import FxScenarioBatchCreate
from app.schemas.regulatory_irr import IrrScenarioBatchCreate
from app.schemas.regulatory_liquidity import LiquidityScenarioBatchCreate
from app.services import (
    regulatory_capital,
    regulatory_forecasting,
    regulatory_ftp,
    regulatory_fx,
    regulatory_irr,
    regulatory_liquidity,
)
from app.services.audit import record_event
from app.services.fact_derivation import DerivationError, DerivationResult, derive_facts

ACTIVATION_EVENT = "bank_data.activated"
FORECAST_BASE_SCENARIO = "base"


def activate_bank_data(
    db: Session, ctx: TenantContext, bank_id: UUID, payload: DataActivationCreate
) -> DataActivationRead:
    _require_actor(ctx)
    bank = _get_bank_or_404(db, ctx, bank_id)

    try:
        derivation = derive_facts(db, ctx, bank.id, payload.as_of_date)
    except DerivationError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error_code": exc.code, "message": exc.message},
        ) from exc
    # Commit the derived facts before the module runs: each run service manages
    # its own commits and snapshots the facts it reads.
    db.commit()

    runs: list[ActivationRunRead] = []
    if payload.run_calculations:
        runs = _run_all_modules(db, ctx, bank.id, derivation.reporting_period_id)

    result = _read(bank, derivation, runs)
    record_event(
        db,
        ctx,
        event_type=ACTIVATION_EVENT,
        entity_type="bank",
        entity_id=bank.id,
        details={
            "as_of_date": payload.as_of_date.isoformat(),
            "reporting_period_id": str(derivation.reporting_period_id),
            "period_label": derivation.period_label,
            "period_created": derivation.period_created,
            "facts_deleted": derivation.facts_deleted,
            "facts_created": derivation.facts_created,
            "run_calculations": payload.run_calculations,
            "reason": payload.reason,
            "warnings": len(result.warnings),
            "modules_succeeded": sum(1 for run in runs if run.status == "succeeded"),
            "modules_failed": sum(1 for run in runs if run.status == "failed"),
            "modules_partial": sum(1 for run in runs if run.status == "partial"),
        },
    )
    db.commit()
    return result


def list_bank_data_activations(
    db: Session, ctx: TenantContext, bank_id: UUID, *, limit: int = 10
) -> DataActivationListRead:
    bank = _get_bank_or_404(db, ctx, bank_id)
    events = db.scalars(
        select(AuditEvent)
        .where(
            AuditEvent.organization_id == ctx.organization_id,
            AuditEvent.event_type == ACTIVATION_EVENT,
            AuditEvent.entity_type == "bank",
            AuditEvent.entity_id == bank.id,
        )
        .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
        .limit(limit)
    )
    activations = [
        DataActivationSummaryRead(
            activated_at=event.created_at,
            as_of_date=event.details.get("as_of_date"),
            period_label=event.details.get("period_label"),
            facts_created=event.details.get("facts_created"),
            modules_succeeded=event.details.get("modules_succeeded"),
            modules_failed=event.details.get("modules_failed"),
            warnings=event.details.get("warnings"),
        )
        for event in events
    ]
    return DataActivationListRead(bank_id=bank.id, activations=activations)


def _run_all_modules(
    db: Session, ctx: TenantContext, bank_id: UUID, period_id: UUID
) -> list[ActivationRunRead]:
    modules: tuple[tuple[str, Callable[[], ActivationRunRead]], ...] = (
        (
            "liquidity",
            lambda: _batch_outcome(
                "liquidity",
                regulatory_liquidity.run_all_liquidity_scenarios(
                    db, ctx, bank_id, LiquidityScenarioBatchCreate(reporting_period_id=period_id)
                ).runs,
                lambda metrics: (
                    f"LCR {_pct(metrics, 'lcr_pct')} · NSFR {_pct(metrics, 'nsfr_pct')}"
                ),
            ),
        ),
        (
            "capital",
            lambda: _batch_outcome(
                "capital",
                regulatory_capital.run_all_capital_scenarios(
                    db, ctx, bank_id, CapitalScenarioBatchCreate(reporting_period_id=period_id)
                ).runs,
                lambda metrics: f"CAR {_pct(metrics, 'car_pct')}",
            ),
        ),
        (
            "irr",
            lambda: _batch_outcome(
                "irr",
                regulatory_irr.run_all_irr_scenarios(
                    db, ctx, bank_id, IrrScenarioBatchCreate(reporting_period_id=period_id)
                ).runs,
                lambda metrics: (
                    f"worst ΔEVE/Tier1 {_pct(metrics, 'worst_eve_change_pct_tier1')} "
                    f"({metrics.get('worst_scenario', '—')})"
                ),
            ),
        ),
        (
            "fx",
            lambda: _batch_outcome(
                "fx",
                regulatory_fx.run_all_fx_scenarios(
                    db, ctx, bank_id, FxScenarioBatchCreate(reporting_period_id=period_id)
                ).runs,
                lambda metrics: f"NOP/Tier1 {_pct(metrics, 'nop_pct_tier1')}",
            ),
        ),
        (
            "ftp",
            lambda: _batch_outcome(
                "ftp",
                regulatory_ftp.run_all_ftp_scenarios(
                    db, ctx, bank_id, FtpScenarioBatchCreate(reporting_period_id=period_id)
                ).runs,
                lambda metrics: f"portfolio NIM {_pct(metrics, 'portfolio_nim_pct')}",
            ),
        ),
        ("forecast", lambda: _forecast_outcome(db, ctx, bank_id, period_id)),
    )

    outcomes: list[ActivationRunRead] = []
    for module, run in modules:
        try:
            outcomes.append(run())
        except HTTPException as exc:
            db.rollback()
            outcomes.append(_failed(module, _http_detail(exc)))
        except Exception as exc:  # noqa: BLE001 - partial success is the contract
            db.rollback()
            outcomes.append(_failed(module, str(exc) or type(exc).__name__))
    return outcomes


def _batch_outcome(
    module: str,
    runs: list[Any],
    headline: Callable[[dict[str, Any]], str],
) -> ActivationRunRead:
    succeeded = sum(1 for run in runs if run.status == "succeeded")
    failed = len(runs) - succeeded
    baseline = next((run for run in runs if run.scenario_code == "baseline"), None)
    headline_text: str | None = None
    error: str | None = None
    if baseline is not None and baseline.status == "succeeded":
        headline_text = headline(baseline.metrics)
    first_error = next((run.error for run in runs if run.error is not None), None)
    if first_error is not None:
        error = first_error.message
    return ActivationRunRead(
        module=module,  # type: ignore[arg-type]
        status="succeeded" if failed == 0 else ("failed" if succeeded == 0 else "partial"),
        scenarios_succeeded=succeeded,
        scenarios_failed=failed,
        headline=headline_text,
        error=error,
    )


def _forecast_outcome(
    db: Session, ctx: TenantContext, bank_id: UUID, period_id: UUID
) -> ActivationRunRead:
    run = regulatory_forecasting.create_forecast_run(
        db,
        ctx,
        bank_id,
        ForecastRunCreate(reporting_period_id=period_id, scenario_code=FORECAST_BASE_SCENARIO),
    )
    succeeded = run.status == "succeeded"
    headline = None
    if succeeded and run.summary is not None:
        headline = f"avg ROE {run.summary.avg_roe_pct}%"
    return ActivationRunRead(
        module="forecast",
        status="succeeded" if succeeded else "failed",
        scenarios_succeeded=1 if succeeded else 0,
        scenarios_failed=0 if succeeded else 1,
        headline=headline,
        error=run.error.message if run.error is not None else None,
    )


def _failed(module: str, error: str) -> ActivationRunRead:
    return ActivationRunRead(
        module=module,  # type: ignore[arg-type]
        status="failed",
        scenarios_succeeded=0,
        scenarios_failed=1,
        headline=None,
        error=error,
    )


def _http_detail(exc: HTTPException) -> str:
    detail = exc.detail
    if isinstance(detail, dict):
        return str(detail.get("message") or detail)
    return str(detail)


def _pct(metrics: dict[str, Any], key: str) -> str:
    value = metrics.get(key)
    if value is None:
        return "—"
    return f"{value}%"


def _read(
    bank: Bank, derivation: DerivationResult, runs: list[ActivationRunRead]
) -> DataActivationRead:
    groups = [
        ActivationGroupRead(
            group=group.group,
            status=group.status,
            rows=group.rows,
            warnings=list(group.warnings),
            note=group.note,
        )
        for group in derivation.groups
    ]
    return DataActivationRead(
        bank_id=bank.id,
        reporting_period_id=derivation.reporting_period_id,
        period_label=derivation.period_label,
        as_of_date=derivation.as_of_date,
        period_created=derivation.period_created,
        facts_deleted=derivation.facts_deleted,
        facts_created=derivation.facts_created,
        groups=groups,
        runs=runs,
        warnings=derivation.warnings,
    )


def _get_bank_or_404(db: Session, ctx: TenantContext, bank_id: UUID) -> Bank:
    bank = db.scalar(
        select(Bank).where(Bank.id == bank_id, Bank.organization_id == ctx.organization_id)
    )
    if bank is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bank not found.")
    return bank


def _require_actor(ctx: TenantContext) -> None:
    if ctx.actor_user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="X-User-Id header is required."
        )
