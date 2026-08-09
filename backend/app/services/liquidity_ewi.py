"""Early-warning indicator framework (LRMD 2026 ¶28(e)–(f)).

The eight BoG-named starter indicators are code-defined; the tenant register
(``liquidity_ewi_indicators``) overrides a starter's Board-set trigger levels
/ enabled state / recovery-plan alignment, or adds custom indicators. The
evaluator computes each indicator from CANONICAL data and stored regulatory
runs only — an indicator whose input is not ingested reports ``no_data``
honestly rather than a fabricated value, and an indicator without Board-set
thresholds reports ``unconfigured`` (value shown, no RAG claim).

Trend indicators (asset growth) need the prior reporting period's canonical
positions; without them the status is ``no_data`` with the reason stated.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal, cast
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.db.base import utc_now
from app.models import (
    Bank,
    BankReportingPeriod,
    CanonicalPosition,
    CanonicalPositionSnapshot,
    LiquidityEwiIndicator,
    RegulatoryMetricResult,
    RegulatoryRun,
)
from app.schemas.liquidity_cfp import (
    EscalationState,
    EwiDirection,
    EwiEvaluationRead,
    EwiRegisterPut,
    EwiStatus,
    EwiUnit,
)
from app.services.audit import record_event
from app.services.jurisdictions import base_currency

_ASSET_TYPES = ("LOAN", "SECURITY_HOLDING", "CASH", "INTERBANK_PLACEMENT", "OTHER_ASSET")
_LIABILITY_TYPES = ("DEPOSIT", "INTERBANK_BORROWING", "OTHER_LIABILITY")
_ACCEPTED_STATUSES = ("accepted", "warning")
_HUNDRED = Decimal("100")
_PCT_Q = Decimal("0.000001")


@dataclass(frozen=True)
class StarterIndicator:
    """One LRMD ¶28(f) named indicator with its computed-metric binding."""

    code: str
    name: str
    description: str
    metric_basis: str
    unit: Literal["pct", "days", "count", "ghs"]
    direction: Literal["above", "below"]


# LRMD ¶28(f) verbatim signal list; metric bases are AequorOS's computable
# bindings over canonical data (documented per indicator, never invented).
STARTER_INDICATORS: tuple[StarterIndicator, ...] = (
    StarterIndicator(
        code="asset_growth_volatile_funding",
        name="Rapid asset growth funded by volatile liabilities",
        description=(
            "Period-over-period growth in total canonical assets, read together "
            "with the volatile share of deposit funding."
        ),
        metric_basis="total-asset growth vs prior reporting period (%)",
        unit="pct",
        direction="above",
    ),
    StarterIndicator(
        code="funding_concentration",
        name="Growing concentration in funding sources",
        description=(
            "Share of total funding held by the five largest counterparties "
            "(granular retail funding without counterparty links is excluded)."
        ),
        metric_basis="top-5 counterparty share of liabilities (%)",
        unit="pct",
        direction="above",
    ),
    StarterIndicator(
        code="currency_mismatch",
        name="Increase in currency mismatches",
        description="Non-base-currency share of total liabilities.",
        metric_basis="foreign-currency share of liabilities (%)",
        unit="pct",
        direction="above",
    ),
    StarterIndicator(
        code="weighted_liability_maturity",
        name="Decline in weighted-average maturity of liabilities",
        description=(
            "Balance-weighted average days to contractual maturity of dated "
            "liabilities; demand liabilities carry no maturity and are excluded."
        ),
        metric_basis="weighted-average liability maturity (days)",
        unit="days",
        direction="below",
    ),
    StarterIndicator(
        code="near_limit_incidents",
        name="Repeated incidents approaching internal or regulatory limits",
        description=(
            "Headline regulatory metrics currently classified amber or red "
            "across the latest baseline liquidity and capital runs."
        ),
        metric_basis="amber/red headline metrics in latest baseline runs (count)",
        unit="count",
        direction="above",
    ),
    StarterIndicator(
        code="earnings_asset_quality",
        name="Deterioration in earnings or asset quality",
        description="IFRS 9 stage-3 share of the loan book, where staging is ingested.",
        metric_basis="stage-3 share of loans (%)",
        unit="pct",
        direction="above",
    ),
    StarterIndicator(
        code="debt_spreads",
        name="Widening of debt or credit-default spreads",
        description=(
            "Secondary-market spread on the institution's own debt; requires "
            "vendor market data that is not yet connected."
        ),
        metric_basis="own-debt spread (bps) — vendor market data required",
        unit="pct",
        direction="above",
    ),
    StarterIndicator(
        code="funding_costs",
        name="Rising cost of funding",
        description=(
            "Balance-weighted average interest rate across liabilities carrying "
            "an ingested rate."
        ),
        metric_basis="weighted-average liability rate (%)",
        unit="pct",
        direction="above",
    ),
)

_STARTERS_BY_CODE = {starter.code: starter for starter in STARTER_INDICATORS}


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


# --- register ---------------------------------------------------------------


def _register_rows(
    db: Session, ctx: TenantContext, bank: Bank
) -> dict[str, LiquidityEwiIndicator]:
    rows = db.scalars(
        select(LiquidityEwiIndicator).where(
            LiquidityEwiIndicator.organization_id == ctx.organization_id,
            LiquidityEwiIndicator.bank_id == bank.id,
        )
    ).all()
    return {row.code: row for row in rows}


def update_register(
    db: Session, ctx: TenantContext, bank_id: str, payload: EwiRegisterPut
) -> None:
    """Audited Board-evidence write of trigger levels / custom indicators."""
    bank = _get_bank_or_404(db, ctx, bank_id)
    existing = _register_rows(db, ctx, bank)
    seen: set[str] = set()
    for entry in payload.indicators:
        if entry.code in seen:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Duplicate indicator code '{entry.code}' in register payload.",
            )
        seen.add(entry.code)
        is_starter = entry.code in _STARTERS_BY_CODE
        if entry.custom and is_starter:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"'{entry.code}' is a directive starter indicator, not a custom one.",
            )
        if not entry.custom and not is_starter:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Unknown indicator code '{entry.code}'. Starter codes are "
                    f"{sorted(_STARTERS_BY_CODE)}; bank-specific indicators must "
                    "set custom=true."
                ),
            )
        if entry.custom and not (entry.name and entry.direction and entry.unit):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Custom indicator '{entry.code}' must carry name, direction "
                    "and unit."
                ),
            )
        row = existing.get(entry.code)
        if row is None:
            row = LiquidityEwiIndicator(
                organization_id=ctx.organization_id, bank_id=bank.id, code=entry.code
            )
            db.add(row)
        row.name = entry.name
        row.description = entry.description
        row.recovery_plan_reference = entry.recovery_plan_reference
        row.watch_threshold = entry.watch_threshold
        row.action_threshold = entry.action_threshold
        row.direction = entry.direction
        row.unit = entry.unit
        row.enabled = entry.enabled
        row.custom = entry.custom
        row.approved_by = payload.approved_by
        row.approval_timestamp = utc_now()
    record_event(
        db,
        ctx,
        event_type="liquidity_ewi.register_updated",
        entity_type="bank",
        entity_id=bank.id,
        details={
            "codes": sorted(seen),
            "approved_by": payload.approved_by,
            "reason": payload.reason,
        },
    )
    db.commit()


# --- canonical reads ---------------------------------------------------------


@dataclass(frozen=True)
class _Row:
    position_type: str
    currency: str
    amount: Decimal
    maturity_days: int | None
    interest_rate: Decimal | None
    ifrs9_stage: int | None
    counterparty_id: UUID | None


def _load_rows(db: Session, ctx: TenantContext, bank: Bank, period_end: date) -> list[_Row]:
    records = db.execute(
        select(CanonicalPositionSnapshot, CanonicalPosition)
        .join(CanonicalPosition, CanonicalPositionSnapshot.position_id == CanonicalPosition.id)
        .where(
            CanonicalPositionSnapshot.organization_id == ctx.organization_id,
            CanonicalPositionSnapshot.bank_id == bank.id,
            CanonicalPositionSnapshot.as_of_date == period_end,
            CanonicalPositionSnapshot.superseded_by.is_(None),
            CanonicalPositionSnapshot.validation_status.in_(_ACCEPTED_STATUSES),
            CanonicalPosition.position_type.in_((*_ASSET_TYPES, *_LIABILITY_TYPES)),
        )
    ).all()
    base = base_currency(bank)
    rows: list[_Row] = []
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
        maturity_days: int | None = None
        if snapshot.contractual_maturity is not None:
            maturity_days = max((snapshot.contractual_maturity - period_end).days, 0)
        rows.append(
            _Row(
                position_type=position.position_type,
                currency=position.currency,
                amount=amount,
                maturity_days=maturity_days,
                interest_rate=snapshot.interest_rate,
                ifrs9_stage=snapshot.ifrs9_stage,
                counterparty_id=snapshot.counterparty_id,
            )
        )
    return rows


def _prior_period(
    db: Session, ctx: TenantContext, bank: Bank, period: BankReportingPeriod
) -> BankReportingPeriod | None:
    return db.scalar(
        select(BankReportingPeriod)
        .where(
            BankReportingPeriod.organization_id == ctx.organization_id,
            BankReportingPeriod.bank_id == bank.id,
            BankReportingPeriod.period_end < period.period_end,
        )
        .order_by(BankReportingPeriod.period_end.desc())
        .limit(1)
    )


# --- per-indicator metric functions ------------------------------------------


def _pct(value: Decimal) -> Decimal:
    return value.quantize(_PCT_Q)


def _total(rows: list[_Row], types: tuple[str, ...]) -> Decimal:
    return sum((row.amount for row in rows if row.position_type in types), Decimal("0"))


def _asset_growth(
    rows: list[_Row], prior_rows: list[_Row] | None
) -> tuple[Decimal | None, str | None]:
    if prior_rows is None:
        return None, "No prior reporting period exists to measure growth against."
    prior_assets = _total(prior_rows, _ASSET_TYPES)
    if prior_assets == 0:
        return None, "The prior period carries no canonical asset positions."
    assets = _total(rows, _ASSET_TYPES)
    return _pct((assets - prior_assets) / prior_assets * _HUNDRED), None


def _top5_funding_share(rows: list[_Row]) -> tuple[Decimal | None, str | None]:
    liabilities = _total(rows, _LIABILITY_TYPES)
    if liabilities == 0:
        return None, "No canonical liability positions are ingested."
    by_counterparty: dict[UUID, Decimal] = {}
    for row in rows:
        if row.position_type in _LIABILITY_TYPES and row.counterparty_id is not None:
            by_counterparty[row.counterparty_id] = (
                by_counterparty.get(row.counterparty_id, Decimal("0")) + row.amount
            )
    top5 = sum(sorted(by_counterparty.values(), reverse=True)[:5], Decimal("0"))
    return _pct(top5 / liabilities * _HUNDRED), None


def _fx_liability_share(rows: list[_Row], base: str) -> tuple[Decimal | None, str | None]:
    liabilities = _total(rows, _LIABILITY_TYPES)
    if liabilities == 0:
        return None, "No canonical liability positions are ingested."
    fx = sum(
        (
            row.amount
            for row in rows
            if row.position_type in _LIABILITY_TYPES and row.currency != base
        ),
        Decimal("0"),
    )
    return _pct(fx / liabilities * _HUNDRED), None


def _weighted_liability_maturity(rows: list[_Row]) -> tuple[Decimal | None, str | None]:
    dated = [
        (row.amount, row.maturity_days)
        for row in rows
        if row.position_type in _LIABILITY_TYPES and row.maturity_days is not None
    ]
    total = sum((amount for amount, _ in dated), Decimal("0"))
    if total == 0:
        return None, "No dated liability positions are ingested."
    weighted = sum((amount * days for amount, days in dated), Decimal("0"))
    return _pct(weighted / total), None


def _near_limit_count(
    db: Session, ctx: TenantContext, bank: Bank, period: BankReportingPeriod
) -> tuple[Decimal | None, str | None]:
    run_ids: list[UUID] = []
    for module in ("liquidity", "capital"):
        run_id = db.scalar(
            select(RegulatoryRun.id)
            .where(
                RegulatoryRun.organization_id == ctx.organization_id,
                RegulatoryRun.bank_id == bank.id,
                RegulatoryRun.reporting_period_id == period.id,
                RegulatoryRun.module == module,
                RegulatoryRun.scenario_code == "baseline",
                RegulatoryRun.status == "succeeded",
            )
            .order_by(RegulatoryRun.created_at.desc(), RegulatoryRun.id.desc())
            .limit(1)
        )
        if run_id is not None:
            run_ids.append(run_id)
    if not run_ids:
        return None, "No succeeded baseline liquidity or capital run exists yet."
    statuses = db.scalars(
        select(RegulatoryMetricResult.status).where(RegulatoryMetricResult.run_id.in_(run_ids))
    ).all()
    return Decimal(sum(1 for value in statuses if value in ("amber", "red"))), None


def _stage3_loan_share(rows: list[_Row]) -> tuple[Decimal | None, str | None]:
    loans = [row for row in rows if row.position_type == "LOAN"]
    total = sum((row.amount for row in loans), Decimal("0"))
    if total == 0:
        return None, "No canonical loan positions are ingested."
    if all(row.ifrs9_stage is None for row in loans):
        return None, "IFRS 9 staging is not ingested on the loan book."
    stage3 = sum((row.amount for row in loans if row.ifrs9_stage == 3), Decimal("0"))
    return _pct(stage3 / total * _HUNDRED), None


def _weighted_funding_cost(rows: list[_Row]) -> tuple[Decimal | None, str | None]:
    rated = [
        (row.amount, row.interest_rate)
        for row in rows
        if row.position_type in _LIABILITY_TYPES and row.interest_rate is not None
    ]
    total = sum((amount for amount, _ in rated), Decimal("0"))
    if total == 0:
        return None, "No liability positions carry an ingested interest rate."
    weighted = sum((amount * rate for amount, rate in rated), Decimal("0"))
    return _pct(weighted / total), None


# --- evaluation --------------------------------------------------------------


def _classify(
    value: Decimal | None,
    watch: Decimal | None,
    action: Decimal | None,
    direction: EwiDirection,
) -> EwiStatus:
    if value is None:
        return "no_data"
    if watch is None and action is None:
        return "unconfigured"
    breaches = (lambda t: value >= t) if direction == "above" else (lambda t: value <= t)
    if action is not None and breaches(action):
        return "action"
    if watch is not None and breaches(watch):
        return "watch"
    return "normal"


def evaluate_ewis(
    db: Session, ctx: TenantContext, bank: Bank, period: BankReportingPeriod
) -> list[EwiEvaluationRead]:
    """Evaluate the merged register over canonical data + stored runs."""
    overrides = _register_rows(db, ctx, bank)
    rows = _load_rows(db, ctx, bank, period.period_end)
    prior = _prior_period(db, ctx, bank, period)
    prior_rows = (
        _load_rows(db, ctx, bank, prior.period_end) if prior is not None else None
    )
    base = base_currency(bank)

    evaluators: dict[str, Callable[[], tuple[Decimal | None, str | None]]] = {
        "asset_growth_volatile_funding": lambda: _asset_growth(rows, prior_rows),
        "funding_concentration": lambda: _top5_funding_share(rows),
        "currency_mismatch": lambda: _fx_liability_share(rows, base),
        "weighted_liability_maturity": lambda: _weighted_liability_maturity(rows),
        "near_limit_incidents": lambda: _near_limit_count(db, ctx, bank, period),
        "earnings_asset_quality": lambda: _stage3_loan_share(rows),
        "debt_spreads": lambda: (
            None,
            "Requires vendor market data, which is not yet connected.",
        ),
        "funding_costs": lambda: _weighted_funding_cost(rows),
    }

    def compute(code: str) -> tuple[Decimal | None, str | None]:
        evaluator = evaluators.get(code)
        if evaluator is None:
            return None, "Custom indicator: monitored manually, no computed binding."
        return evaluator()

    def prior_compute(code: str) -> Decimal | None:
        if prior_rows is None:
            return None
        if code == "funding_concentration":
            return _top5_funding_share(prior_rows)[0]
        if code == "currency_mismatch":
            return _fx_liability_share(prior_rows, base)[0]
        if code == "weighted_liability_maturity":
            return _weighted_liability_maturity(prior_rows)[0]
        if code == "funding_costs":
            return _weighted_funding_cost(prior_rows)[0]
        return None

    evaluations: list[EwiEvaluationRead] = []
    codes = [starter.code for starter in STARTER_INDICATORS] + sorted(
        code for code, row in overrides.items() if row.custom
    )
    for code in codes:
        starter = _STARTERS_BY_CODE.get(code)
        row = overrides.get(code)
        if row is not None and not row.enabled:
            continue
        name = (row.name if row and row.name else None) or (starter.name if starter else code)
        direction = cast(
            EwiDirection,
            (row.direction if row else None) or (starter.direction if starter else "above"),
        )
        unit = cast(
            EwiUnit, (row.unit if row else None) or (starter.unit if starter else "pct")
        )
        value, detail = compute(code)
        watch = row.watch_threshold if row else None
        action = row.action_threshold if row else None
        evaluations.append(
            EwiEvaluationRead(
                code=code,
                name=name,
                description=(row.description if row and row.description else None)
                or (starter.description if starter else None),
                recovery_plan_reference=row.recovery_plan_reference if row else None,
                metric_basis=starter.metric_basis if starter else "manual",
                unit=unit,
                direction=direction,
                value=value,
                prior_value=prior_compute(code),
                watch_threshold=watch,
                action_threshold=action,
                status=_classify(value, watch, action, direction),
                detail=detail,
                custom=bool(row.custom) if row else False,
                enabled=True,
            )
        )
    return evaluations


def escalation_state(
    evaluations: list[EwiEvaluationRead], cfp_active: bool
) -> EscalationState:
    if cfp_active:
        return "cfp_active"
    statuses = {evaluation.status for evaluation in evaluations}
    if "action" in statuses:
        return "escalation"
    if "watch" in statuses:
        return "heightened_monitoring"
    return "normal"
