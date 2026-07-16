"""Per-module freshness: does the live view lead the last official filing run?

For each module it compares the current baseline input hash (from the upserted
``live_metrics`` row, or recomputed with the module's own ``current_input_hash``)
against the latest succeeded official ``RegulatoryRun.input_hash``. A module is
stale when the hashes differ or no official run exists yet — i.e. current data
has moved on since the last regulatory filing run.
"""

from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import Bank, BankReportingPeriod, LiveMetric, RegulatoryRun
from app.schemas.live import BankFreshnessRead, FreshnessModuleRead
from app.services import (
    regulatory_capital,
    regulatory_ftp,
    regulatory_fx,
    regulatory_irr,
    regulatory_liquidity,
)

# The scenario whose immutable run carries the comparable baseline input hash.
_BASELINE_SCENARIO = {
    "liquidity": "baseline",
    "capital": "baseline",
    "irr": "baseline",
    "fx": "baseline",
    "ftp": "baseline",
    "forecast": "base",
}
_HashFn = Callable[[Session, TenantContext, Bank, BankReportingPeriod], str | None]
_CURRENT_HASH: dict[str, _HashFn] = {
    "liquidity": regulatory_liquidity.current_input_hash,
    "capital": regulatory_capital.current_input_hash,
    "irr": regulatory_irr.current_input_hash,
    "fx": regulatory_fx.current_input_hash,
    "ftp": regulatory_ftp.current_input_hash,
}
_MODULES = ("liquidity", "capital", "irr", "fx", "ftp", "forecast")


def get_bank_freshness(
    db: Session,
    ctx: TenantContext,
    bank_id: UUID,
    reporting_period_id: UUID | None = None,
) -> BankFreshnessRead:
    bank = _get_bank_or_404(db, ctx, bank_id)
    period = _resolve_period(db, ctx, bank, reporting_period_id)
    if period is None:
        return BankFreshnessRead(
            bank_id=bank.id,
            reporting_period_id=None,
            period_label=None,
            modules=[],
            is_stale=False,
        )

    live_rows = {
        row.module: row
        for row in db.scalars(
            select(LiveMetric).where(
                LiveMetric.organization_id == ctx.organization_id,
                LiveMetric.bank_id == bank.id,
                LiveMetric.reporting_period_id == period.id,
            )
        )
    }

    modules: list[FreshnessModuleRead] = []
    any_stale = False
    for module in _MODULES:
        live_row = live_rows.get(module)
        if live_row is not None:
            live_hash = live_row.computed_from_input_hash
            computed_at = live_row.computed_at
        else:
            hash_fn = _CURRENT_HASH.get(module)
            live_hash = hash_fn(db, ctx, bank, period) if hash_fn is not None else None
            computed_at = None

        official_run = _latest_official_run(db, ctx, bank, period, module)
        official_hash = official_run.input_hash if official_run is not None else None
        official_run_at = (
            (official_run.completed_at or official_run.created_at)
            if official_run is not None
            else None
        )
        is_stale = official_hash is None or live_hash != official_hash
        any_stale = any_stale or is_stale
        modules.append(
            FreshnessModuleRead(
                module=module,  # type: ignore[arg-type]
                live_hash=live_hash,
                official_run_hash=official_hash,
                is_stale=is_stale,
                computed_at=computed_at,
                official_run_at=official_run_at,
            )
        )

    return BankFreshnessRead(
        bank_id=bank.id,
        reporting_period_id=period.id,
        period_label=period.label,
        modules=modules,
        is_stale=any_stale,
    )


def _latest_official_run(
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    period: BankReportingPeriod,
    module: str,
) -> RegulatoryRun | None:
    return db.scalar(
        select(RegulatoryRun)
        .where(
            RegulatoryRun.organization_id == ctx.organization_id,
            RegulatoryRun.bank_id == bank.id,
            RegulatoryRun.reporting_period_id == period.id,
            RegulatoryRun.module == module,
            RegulatoryRun.scenario_code == _BASELINE_SCENARIO[module],
            RegulatoryRun.status == "succeeded",
        )
        .order_by(RegulatoryRun.created_at.desc(), RegulatoryRun.id.desc())
        .limit(1)
    )


def _resolve_period(
    db: Session, ctx: TenantContext, bank: Bank, reporting_period_id: UUID | None
) -> BankReportingPeriod | None:
    if reporting_period_id is not None:
        period = db.scalar(
            select(BankReportingPeriod).where(
                BankReportingPeriod.id == reporting_period_id,
                BankReportingPeriod.organization_id == ctx.organization_id,
                BankReportingPeriod.bank_id == bank.id,
            )
        )
        if period is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Reporting period not found."
            )
        return period
    return db.scalar(
        select(BankReportingPeriod)
        .where(
            BankReportingPeriod.organization_id == ctx.organization_id,
            BankReportingPeriod.bank_id == bank.id,
        )
        .order_by(BankReportingPeriod.period_end.desc())
        .limit(1)
    )


def _get_bank_or_404(db: Session, ctx: TenantContext, bank_id: UUID) -> Bank:
    bank = db.scalar(
        select(Bank).where(Bank.id == bank_id, Bank.organization_id == ctx.organization_id)
    )
    if bank is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bank not found.")
    return bank
