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
from dataclasses import dataclass
from datetime import date, datetime
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
    LiveMetricSnapshot,
    User,
)
from app.services import (
    data_activation,
    implied_rating,
    regulatory_capital,
    regulatory_forecasting,
    regulatory_ftp,
    regulatory_fx,
    regulatory_irr,
    regulatory_liquidity,
)
from app.services.audit import record_event
from app.services.fact_derivation import DerivationError, derive_current_facts, derive_facts
from app.services.live_types import LiveFindingSpec, LiveModuleResult

REFRESH_EVENT = "bank_data.refreshed"
OFFICIAL_EVENT = "official_run.completed"
FORECAST_MODULE = "forecast"

# Modules with a current canonical-data compute path. No function here writes a
# RegulatoryRun: official snapshots are created only by ``run_official``.
_ComputeLive = Callable[[Session, TenantContext, Bank, BankReportingPeriod], LiveModuleResult]
_CHEAP_MODULES: tuple[tuple[str, _ComputeLive], ...] = (
    ("liquidity", regulatory_liquidity.compute_live),
    ("capital", regulatory_capital.compute_live),
    ("irr", regulatory_irr.compute_live),
    ("fx", regulatory_fx.compute_live),
    ("ftp", regulatory_ftp.compute_live),
    ("rating", implied_rating.compute_live),
    ("forecast", regulatory_forecasting.compute_live),
)


class PipelineError(Exception):
    """A refresh/official job could not run (missing bank or payload)."""


@dataclass(frozen=True)
class RecomputeOutcome:
    """Result of a cheap-tier live recompute."""

    period: BankReportingPeriod | None
    modules_ok: list[str]
    modules_failed: dict[str, str]
    skipped_reason: str | None


def recompute_modules(
    session: Session, ctx: TenantContext, bank: Bank, period: BankReportingPeriod
) -> tuple[list[str], dict[str, str]]:
    """Recompute every module's live view from the CURRENT facts and upsert the
    ``live_metrics`` + findings. Creates **zero** ``RegulatoryRun`` rows.

    This is the light, read-safe half of the live tier: it does NOT re-derive
    facts (no mass delete/insert), so it is fast and non-blocking on the request
    path. The on-read live view (``live_view``) calls this so a methodology or
    parameter change re-flows instantly and cheaply. Fact re-derivation — the
    heavy half — happens only in :func:`recompute_live` (the ingestion-triggered
    background job), never synchronously on a read.
    """
    modules_ok: list[str] = []
    modules_failed: dict[str, str] = {}
    for module, compute in _CHEAP_MODULES:
        try:
            result = compute(session, ctx, bank, period)
            _upsert_live_metric(
                session,
                ctx,
                bank,
                period,
                module,
                result.metrics,
                result.status,
                result.input_hash,
                result.engine_version,
                result.source_as_of_date,
            )
            _reconcile_findings(
                session,
                ctx,
                bank,
                period,
                module,
                result.findings,
                result.source_as_of_date,
            )
            session.commit()
            modules_ok.append(module)
        except Exception as exc:  # noqa: BLE001 - partial success is the contract
            session.rollback()
            message = str(exc) or type(exc).__name__
            modules_failed[module] = message
            _upsert_live_failure(session, ctx, bank, period, module, message)
            session.commit()

    return modules_ok, modules_failed


def recompute_live(
    session: Session, ctx: TenantContext, bank: Bank, as_of: date
) -> RecomputeOutcome:
    """Full cheap-tier recompute from current canonical state.

    The live materialisation is independent of the historical fact plane;
    only explicit official runs derive period-keyed ``BankFinancialFact`` rows.
    """
    try:
        derive_current_facts(session, ctx, bank.id, as_of)
    except DerivationError as exc:
        session.rollback()
        if exc.code == "no_canonical_data":
            # Nothing to compute yet — a benign no-op.
            return RecomputeOutcome(None, [], {}, exc.code)
        raise
    session.commit()

    period = _ensure_live_period(session, ctx, bank, as_of)
    modules_ok, modules_failed = recompute_modules(session, ctx, bank, period)
    return RecomputeOutcome(period, modules_ok, modules_failed, None)


def run_refresh(session: Session, job: Job) -> None:
    """Cheap-tier worker handler: proactively warm the live view for a bank.

    Delegates to :func:`recompute_live` (the same path the on-read live view
    uses) and records the refresh audit event. Idempotent.
    """
    ctx = _ctx_from_job(session, job)
    bank = _bank_or_error(session, ctx, job)
    as_of = _as_of_from_payload(job)

    outcome = recompute_live(session, ctx, bank, as_of)
    if outcome.skipped_reason is not None or outcome.period is None:
        job.progress = {
            "status": "skipped",
            "reason": outcome.skipped_reason or "no_period",
            "as_of_date": as_of.isoformat(),
        }
        return

    record_event(
        session,
        ctx,
        event_type=REFRESH_EVENT,
        entity_type="bank",
        entity_id=bank.id,
        details={
            "as_of_date": as_of.isoformat(),
            "reporting_period_id": str(outcome.period.id),
            "modules_ok": outcome.modules_ok,
            "modules_failed": sorted(outcome.modules_failed),
        },
    )
    session.commit()
    job.progress = {
        "as_of_date": as_of.isoformat(),
        "modules_ok": outcome.modules_ok,
        "modules_failed": outcome.modules_failed,
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


def _upsert_live_metric(  # noqa: PLR0913 - one upsert carries the full live row
    session: Session,
    ctx: TenantContext,
    bank: Bank,
    period: BankReportingPeriod,
    module: str,
    metrics: dict[str, Any],
    status: str,
    input_hash: str | None,
    engine_version: str | None = None,
    source_as_of_date: date | None = None,
) -> None:
    existing = session.scalar(
        select(LiveMetric).where(
            LiveMetric.organization_id == ctx.organization_id,
            LiveMetric.bank_id == bank.id,
            LiveMetric.module == module,
        )
    )
    now = utc_now()
    if existing is None:
        session.add(
            LiveMetric(
                organization_id=ctx.organization_id,
                bank_id=bank.id,
                source_fact_period_id=None,
                source_as_of_date=source_as_of_date or period.period_end,
                module=module,
                metrics=metrics,
                status=status,
                computed_from_input_hash=input_hash,
                engine_version=engine_version or f"live-{module}",
                calculation_generation=1,
                pipeline_state="ready",
                pipeline_error=None,
                computed_at=now,
            )
        )
    else:
        existing.metrics = metrics
        existing.status = status
        existing.computed_from_input_hash = input_hash
        existing.source_fact_period_id = None
        existing.source_as_of_date = source_as_of_date or period.period_end
        existing.engine_version = engine_version or f"live-{module}"
        existing.calculation_generation += 1
        existing.pipeline_state = "ready"
        existing.pipeline_error = None
        existing.computed_at = now
    _upsert_live_snapshot(session, ctx, bank, period, module, metrics, status, now)
    session.flush()


def _upsert_live_failure(
    session: Session,
    ctx: TenantContext,
    bank: Bank,
    period: BankReportingPeriod,
    module: str,
    error: str,
) -> None:
    """Expose a failed module refresh without altering its last good metric.

    The current calculation is not fresh, so callers get an explicit failed
    state and diagnostic rather than silently reading an old value as current.
    """
    existing = session.scalar(
        select(LiveMetric).where(
            LiveMetric.organization_id == ctx.organization_id,
            LiveMetric.bank_id == bank.id,
            LiveMetric.module == module,
        )
    )
    now = utc_now()
    if existing is None:
        session.add(
            LiveMetric(
                organization_id=ctx.organization_id,
                bank_id=bank.id,
                source_fact_period_id=None,
                source_as_of_date=period.period_end,
                module=module,
                metrics={},
                status="na",
                computed_from_input_hash=None,
                engine_version=f"live-{module}",
                calculation_generation=1,
                pipeline_state="failed",
                pipeline_error=error,
                computed_at=now,
            )
        )
    else:
        existing.source_fact_period_id = period.id
        existing.source_as_of_date = period.period_end
        existing.calculation_generation += 1
        existing.pipeline_state = "failed"
        existing.pipeline_error = error
        existing.computed_at = now
    _upsert_live_snapshot(session, ctx, bank, period, module, {}, "na", now)
    session.flush()


def _upsert_live_snapshot(  # noqa: PLR0913 - mirrors the live-metric upsert
    session: Session,
    ctx: TenantContext,
    bank: Bank,
    period: BankReportingPeriod,
    module: str,
    metrics: dict[str, Any],
    status: str,
    computed_at: datetime,
) -> None:
    """Plane-2 EOD ladder: the day's LAST refresh is the close.

    One row per (bank, calendar day, module), overwritten on every refresh —
    past days therefore hold their end-of-day state and today's row is the
    live edge. Desk prior-close deltas and daily sparklines read this series.
    """
    snapshot_date = computed_at.date()
    existing = session.scalar(
        select(LiveMetricSnapshot).where(
            LiveMetricSnapshot.organization_id == ctx.organization_id,
            LiveMetricSnapshot.bank_id == bank.id,
            LiveMetricSnapshot.snapshot_date == snapshot_date,
            LiveMetricSnapshot.module == module,
        )
    )
    if existing is None:
        session.add(
            LiveMetricSnapshot(
                organization_id=ctx.organization_id,
                bank_id=bank.id,
                reporting_period_id=period.id,
                snapshot_date=snapshot_date,
                module=module,
                metrics=metrics,
                status=status,
                computed_at=computed_at,
            )
        )
    else:
        existing.reporting_period_id = period.id
        existing.metrics = metrics
        existing.status = status
        existing.computed_at = computed_at


def _reconcile_findings(  # noqa: PLR0913 - one reconcile carries the full scope key
    session: Session,
    ctx: TenantContext,
    bank: Bank,
    period: BankReportingPeriod,
    module: str,
    specs: tuple[LiveFindingSpec, ...],
    source_as_of_date: date | None = None,
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
                    source_fact_period_id=None,
                    source_as_of_date=source_as_of_date or period.period_end,
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
    for finding in by_rule.values():
        finding.source_fact_period_id = None
        finding.source_as_of_date = source_as_of_date or period.period_end
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


def _ensure_live_period(
    session: Session, ctx: TenantContext, bank: Bank, as_of: date
) -> BankReportingPeriod:
    """Create only the snapshot-ladder provenance row needed by the live plane."""
    period = _find_period(session, ctx, bank, as_of)
    if period is not None:
        return period
    period = BankReportingPeriod(
        organization_id=ctx.organization_id,
        bank_id=bank.id,
        period_start=as_of.replace(day=1),
        period_end=as_of,
        label=f"{as_of.year:04d}-{as_of.month:02d}",
        status="open",
    )
    session.add(period)
    session.flush()
    return period


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
