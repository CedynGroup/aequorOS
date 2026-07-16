"""The automatic two-tier live pipeline: ingestion's background half.

``run_refresh`` is the cheap tier: it re-derives module facts from current
canonical state, recomputes each module's baseline live view (upserting one
``live_metrics`` row per module), reconciles limit findings, and records a
refresh event — creating **zero** ``RegulatoryRun`` rows. ``run_official`` is
the immutable tier: it reuses the exact 22-scenario + forecast activation path
so scheduled and on-demand filing runs mint the same immutable runs as before.

Both are worker handlers: ``(session, job)`` where ``job.payload`` carries
``as_of_date`` (and, for official runs, an optional ``actor_user_id``).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.db.base import utc_now
from app.models import (
    Bank,
    BankFinancialFact,
    BankReportingPeriod,
    Job,
    LiveFinding,
    LiveMetric,
    RegulatoryRun,
    User,
)
from app.services import (
    data_activation,
    regulatory_capital,
    regulatory_ftp,
    regulatory_fx,
    regulatory_irr,
    regulatory_liquidity,
)
from app.services.audit import record_event
from app.services.fact_derivation import DerivationError, derive_facts
from app.services.live_types import LiveFindingSpec, LiveModuleResult

REFRESH_EVENT = "bank_data.refreshed"
OFFICIAL_EVENT = "official_run.completed"
FORECAST_MODULE = "forecast"

# Modules with a cheap inline compute path (no engine run persisted). Forecast is
# excluded: it is expensive (LSTM) and inherently official-run-based, so the live
# forecast row reflects the latest immutable forecast run instead.
_ComputeLive = Callable[[Session, TenantContext, Bank, BankReportingPeriod], LiveModuleResult]
_CHEAP_MODULES: tuple[tuple[str, _ComputeLive], ...] = (
    ("liquidity", regulatory_liquidity.compute_live),
    ("capital", regulatory_capital.compute_live),
    ("irr", regulatory_irr.compute_live),
    ("fx", regulatory_fx.compute_live),
    ("ftp", regulatory_ftp.compute_live),
)


class PipelineError(Exception):
    """A refresh/official job could not run (missing bank or payload)."""


def run_refresh(session: Session, job: Job) -> None:
    """Cheap tier: derive facts, recompute live metrics + findings for a bank.

    Idempotent — re-running upserts the same ``live_metrics`` rows and reconciles
    findings (continuing breaches keep their identity; cleared breaches are
    superseded). No ``RegulatoryRun`` row is ever created or mutated.
    """
    ctx = _ctx_from_job(session, job)
    bank = _bank_or_error(session, ctx, job)
    as_of = _as_of_from_payload(job)

    try:
        derivation = derive_facts(session, ctx, bank.id, as_of)
    except DerivationError as exc:
        session.rollback()
        if exc.code == "no_canonical_data":
            # Nothing to compute yet — a benign no-op, not a failed job.
            job.progress = {
                "status": "skipped",
                "reason": exc.code,
                "as_of_date": as_of.isoformat(),
            }
            return
        raise
    session.commit()

    period = _get_period(session, ctx, bank, derivation.reporting_period_id)
    modules_ok: list[str] = []
    modules_failed: dict[str, str] = {}
    for module, compute in _CHEAP_MODULES:
        try:
            result = compute(session, ctx, bank, period)
            _upsert_live_metric(
                session, ctx, bank, period, module, result.metrics, result.status, result.input_hash
            )
            _reconcile_findings(session, ctx, bank, period, module, result.findings)
            session.commit()
            modules_ok.append(module)
        except Exception as exc:  # noqa: BLE001 - partial success is the contract
            session.rollback()
            modules_failed[module] = str(exc) or type(exc).__name__

    try:
        if _refresh_forecast_live(session, ctx, bank, period):
            session.commit()
            modules_ok.append(FORECAST_MODULE)
    except Exception:  # noqa: BLE001 - forecast reflection is best-effort
        session.rollback()

    record_event(
        session,
        ctx,
        event_type=REFRESH_EVENT,
        entity_type="bank",
        entity_id=bank.id,
        details={
            "as_of_date": as_of.isoformat(),
            "reporting_period_id": str(period.id),
            "modules_ok": modules_ok,
            "modules_failed": sorted(modules_failed),
        },
    )
    session.commit()
    job.progress = {
        "as_of_date": as_of.isoformat(),
        "modules_ok": modules_ok,
        "modules_failed": modules_failed,
    }


def run_official(session: Session, job: Job) -> None:
    """Immutable tier: mint the official 22-scenario + forecast runs for a bank.

    Reuses ``data_activation.run_official_modules``. Facts are re-derived only
    when the period has none, so repeat official runs on unchanged facts
    reproduce the same immutable ``input_hash`` per run.
    """
    ctx = _ctx_from_job(session, job, require_actor=True)
    bank = _bank_or_error(session, ctx, job)
    as_of = _as_of_from_payload(job)

    period = _find_period(session, ctx, bank, as_of)
    if period is None or not _has_facts(session, ctx, bank, period):
        derivation = derive_facts(session, ctx, bank.id, as_of)
        session.commit()
        period_id = derivation.reporting_period_id
    else:
        period_id = period.id

    runs = data_activation.run_official_modules(session, ctx, bank.id, period_id)
    record_event(
        session,
        ctx,
        event_type=OFFICIAL_EVENT,
        entity_type="bank",
        entity_id=bank.id,
        details={
            "as_of_date": as_of.isoformat(),
            "reporting_period_id": str(period_id),
            "modules_succeeded": sum(1 for run in runs if run.status == "succeeded"),
            "modules_failed": sum(1 for run in runs if run.status in ("failed", "partial")),
        },
    )
    session.commit()
    job.progress = {
        "as_of_date": as_of.isoformat(),
        "reporting_period_id": str(period_id),
        "modules": [run.module for run in runs],
    }


def _refresh_forecast_live(
    session: Session, ctx: TenantContext, bank: Bank, period: BankReportingPeriod
) -> bool:
    """Reflect the latest succeeded official forecast run into a live row.

    Forecast has no cheap inline path, so its live view mirrors the newest
    immutable forecast run for the period. Returns False when none exists yet.
    """
    run = session.scalar(
        select(RegulatoryRun)
        .where(
            RegulatoryRun.organization_id == ctx.organization_id,
            RegulatoryRun.bank_id == bank.id,
            RegulatoryRun.reporting_period_id == period.id,
            RegulatoryRun.module == FORECAST_MODULE,
            RegulatoryRun.status == "succeeded",
        )
        .order_by(RegulatoryRun.created_at.desc(), RegulatoryRun.id.desc())
        .limit(1)
    )
    if run is None:
        return False
    metrics = {
        key: value
        for key, value in run.metrics.items()
        if isinstance(value, (str, int, float, bool))
    }
    metrics["official_run_id"] = str(run.id)
    _upsert_live_metric(session, ctx, bank, period, FORECAST_MODULE, metrics, "na", run.input_hash)
    return True


def _upsert_live_metric(  # noqa: PLR0913 - one upsert carries the full live row
    session: Session,
    ctx: TenantContext,
    bank: Bank,
    period: BankReportingPeriod,
    module: str,
    metrics: dict[str, Any],
    status: str,
    input_hash: str | None,
) -> None:
    existing = session.scalar(
        select(LiveMetric).where(
            LiveMetric.organization_id == ctx.organization_id,
            LiveMetric.bank_id == bank.id,
            LiveMetric.reporting_period_id == period.id,
            LiveMetric.module == module,
        )
    )
    now = utc_now()
    if existing is None:
        session.add(
            LiveMetric(
                organization_id=ctx.organization_id,
                bank_id=bank.id,
                reporting_period_id=period.id,
                module=module,
                metrics=metrics,
                status=status,
                computed_from_input_hash=input_hash,
                computed_at=now,
            )
        )
    else:
        existing.metrics = metrics
        existing.status = status
        existing.computed_from_input_hash = input_hash
        existing.computed_at = now
    session.flush()


def _reconcile_findings(  # noqa: PLR0913 - one reconcile carries the full scope key
    session: Session,
    ctx: TenantContext,
    bank: Bank,
    period: BankReportingPeriod,
    module: str,
    specs: tuple[LiveFindingSpec, ...],
) -> None:
    """Reconcile a module's open live findings against a fresh breach set.

    Continuing breaches keep their row (and ``created_at``); new breaches are
    inserted open; breaches that cleared are superseded.
    """
    existing = list(
        session.scalars(
            select(LiveFinding).where(
                LiveFinding.organization_id == ctx.organization_id,
                LiveFinding.bank_id == bank.id,
                LiveFinding.reporting_period_id == period.id,
                LiveFinding.module == module,
                LiveFinding.status == "open",
            )
        )
    )
    by_rule = {finding.rule_id: finding for finding in existing}
    fresh_rules: set[str] = set()
    for spec in specs:
        fresh_rules.add(spec.rule_id)
        current = by_rule.get(spec.rule_id)
        if current is not None:
            current.severity = spec.severity
            current.message = spec.message
            current.metric = spec.metric
        else:
            session.add(
                LiveFinding(
                    organization_id=ctx.organization_id,
                    bank_id=bank.id,
                    reporting_period_id=period.id,
                    module=module,
                    rule_id=spec.rule_id,
                    severity=spec.severity,
                    status="open",
                    message=spec.message,
                    metric=spec.metric,
                )
            )
    for rule_id, finding in by_rule.items():
        if rule_id not in fresh_rules:
            finding.status = "superseded"
    session.flush()


def _ctx_from_job(session: Session, job: Job, *, require_actor: bool = False) -> TenantContext:
    actor_raw = job.payload.get("actor_user_id")
    actor_id = UUID(str(actor_raw)) if actor_raw else None
    if require_actor and actor_id is None:
        actor_id = session.scalar(
            select(User.id)
            .where(User.organization_id == job.organization_id, User.is_active.is_(True))
            .order_by(User.created_at)
            .limit(1)
        )
    return TenantContext(organization_id=job.organization_id, actor_user_id=actor_id)


def _bank_or_error(session: Session, ctx: TenantContext, job: Job) -> Bank:
    if job.bank_id is None:
        raise PipelineError("Job has no bank_id.")
    bank = session.scalar(
        select(Bank).where(Bank.id == job.bank_id, Bank.organization_id == ctx.organization_id)
    )
    if bank is None:
        raise PipelineError(f"Bank {job.bank_id} not found for organization.")
    return bank


def _as_of_from_payload(job: Job) -> date:
    raw = job.payload.get("as_of_date")
    if not raw:
        raise PipelineError("Job payload is missing as_of_date.")
    return date.fromisoformat(str(raw))


def _get_period(
    session: Session, ctx: TenantContext, bank: Bank, period_id: UUID
) -> BankReportingPeriod:
    period = session.scalar(
        select(BankReportingPeriod).where(
            BankReportingPeriod.id == period_id,
            BankReportingPeriod.organization_id == ctx.organization_id,
            BankReportingPeriod.bank_id == bank.id,
        )
    )
    if period is None:  # pragma: no cover - derivation just created it
        raise PipelineError("Reporting period vanished after derivation.")
    return period


def _find_period(
    session: Session, ctx: TenantContext, bank: Bank, as_of: date
) -> BankReportingPeriod | None:
    return session.scalar(
        select(BankReportingPeriod).where(
            BankReportingPeriod.organization_id == ctx.organization_id,
            BankReportingPeriod.bank_id == bank.id,
            BankReportingPeriod.period_end == as_of,
        )
    )


def _has_facts(
    session: Session, ctx: TenantContext, bank: Bank, period: BankReportingPeriod
) -> bool:
    count = session.scalar(
        select(func.count())
        .select_from(BankFinancialFact)
        .where(
            BankFinancialFact.organization_id == ctx.organization_id,
            BankFinancialFact.bank_id == bank.id,
            BankFinancialFact.reporting_period_id == period.id,
        )
    )
    return bool(count)
