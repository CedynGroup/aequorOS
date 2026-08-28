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
from app.domain.authority.registry import MetricFamily
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
    filing_reconciliation,
    implied_rating,
    module_scope,
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
from app.services.reporting_periods import new_snapshot_period

REFRESH_EVENT = "bank_data.refreshed"
OFFICIAL_EVENT = "official_run.completed"
FORECAST_MODULE = "forecast"

#: Stamped INTO the live metrics payload — not only beside it — whenever the
#: book behind a live figure fails the balance-sheet identity. ``pipeline_state``
#: already says so on the row, but a consumer that reads only ``metrics`` (the
#: daily snapshot ladder has nothing else) would otherwise see a bare number
#: indistinguishable from a sound one. Present ONLY when blocked, so a
#: reconciled book's payload is byte-identical to what it was before.
RECONCILIATION_METRIC_KEY = "reconciliation_status"
RECONCILIATION_METRIC_BLOCKED = "blocked"

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

#: Live-engine module key → the institution_types ``default_modules`` slug it is
#: scoped by (docs/sdi.md §3.2). A module absent from the tenant's set is not
#: computed — an SDI does not run FX/FTP, so no empty FX/FTP live-metric or finding
#: reaches its Alerts / live-summary. A universal bank has every slug, so the filter
#: is a no-op (byte-identical). ``rating`` (implied rating) rides the market-data set.
_MODULE_SCOPE_KEY: dict[str, str] = {
    "liquidity": "liquidity",
    "capital": "capital",
    "irr": "irrbb",
    "fx": "fx",
    "ftp": "ftp",
    "rating": "markets",
    "forecast": "forecasting",
}


#: Live modules whose outputs are authoritative only for a declared institution
#: class. The declaration itself lives in WS-A's metric authority registry; this
#: maps the live module key onto the metric family to look up there, so the live
#: tier can never disagree with the registry about who a metric belongs to.
_MODULE_METRIC_FAMILY: dict[str, MetricFamily] = {
    "forecast": MetricFamily.FORECAST,
}


def _scoped_modules(
    db: Session, bank: Bank
) -> tuple[tuple[str, _ComputeLive], ...]:
    """The cheap-tier modules the tenant's institution type is entitled to run.

    Delegates to ``module_scope`` — the ONE authority, shared with the official
    filing tier (``data_activation``), so the two tiers cannot disagree about
    which modules an institution runs. The entitlement, metric-authority and
    engine-substitution rules that used to live inline here now live there,
    unchanged in behaviour and pinned by ``tests/services/test_module_scope.py``.
    """
    return tuple(
        (module, compute)
        for module, compute in _CHEAP_MODULES
        if module_scope.runs_module(db, bank, module)
    )


class PipelineError(Exception):
    """A refresh/official job could not run (missing bank or payload)."""


class TransientLiveRefreshError(PipelineError):
    """One or more live modules raised and the worker must retry this job.

    Successful ``availability=unavailable`` results never enter this path: they
    are structural outcomes and remain stable until an authoritative mutation
    enqueues a new refresh. This exception carries only genuine module errors
    to the worker's existing bounded queue backoff.
    """

    def __init__(self, modules_failed: dict[str, str]) -> None:
        self.modules_failed = dict(modules_failed)
        modules = ", ".join(sorted(modules_failed))
        super().__init__(f"Transient live-module failure: {modules}")


@dataclass(frozen=True)
class RecomputeOutcome:
    """Result of a cheap-tier live recompute."""

    period: BankReportingPeriod | None
    modules_ok: list[str]
    modules_failed: dict[str, str]
    skipped_reason: str | None
    #: The balance-sheet control's refusal message when the live derivation ran
    #: on a book that does not reconcile, else ``None``. The live plane keeps
    #: materialising — an operator has to see the broken book to fix it — but
    #: every module row it writes carries this and ``pipeline_state="blocked"``,
    #: so no reader can take the figures for sound ones (audit 2026-08-22 D-1).
    reconciliation_block: str | None = None


def recompute_modules(
    session: Session,
    ctx: TenantContext,
    bank: Bank,
    period: BankReportingPeriod,
    *,
    reconciliation_block: str | None = None,
) -> tuple[list[str], dict[str, str]]:
    """Recompute every module's live view from the CURRENT facts and upsert the
    ``live_metrics`` + findings. Creates **zero** ``RegulatoryRun`` rows.

    ``reconciliation_block`` is the balance-sheet control's refusal message when
    the facts underneath were derived from a book that does not reconcile. It
    does not suppress the computation — the whole point of the live plane is
    that the operator can see the state they must repair — but it stamps every
    row it writes ``pipeline_state="blocked"`` with the message, so the figures
    are never served as sound ones.

    This is the light half of the live tier: it does NOT re-derive facts (no
    mass delete/insert). The background refresh calls it after deriving current
    facts; reads only consume the persisted rows. Ingestion, market-data,
    methodology, parameter, tenant assumption/threshold/haircut, entitlement,
    and reconciliation mutations enqueue that refresh at the point the
    corresponding input generation changes.
    """
    modules_ok: list[str] = []
    modules_failed: dict[str, str] = {}
    for module, compute in _scoped_modules(session, bank):
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
                reconciliation_block=reconciliation_block,
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
        derivation = derive_current_facts(session, ctx, bank.id, as_of)
    except DerivationError as exc:
        session.rollback()
        if exc.code == "no_canonical_data":
            # Nothing to compute yet — a benign no-op.
            return RecomputeOutcome(None, [], {}, exc.code)
        raise
    session.commit()

    # The verdict the derivation already computed. It used to be discarded here
    # (audit 2026-08-22 D-1), which is how a tenant 3.68% out of balance came to
    # serve seven live-metric rows, every one ``pipeline_state='ready'``.
    identity = derivation.reconciliation
    reconciliation_block = (
        identity.message(bank.currency or "")
        if identity is not None and identity.blocks_filing
        else None
    )

    period = _ensure_live_period(session, ctx, bank, as_of)
    # COMMIT the snapshot row before any module runs. ``_ensure_live_period``
    # only flushes, and ``recompute_modules`` calls ``session.rollback()`` on the
    # first module that fails — which discarded the pending period along with the
    # failed module's work. The row is PROVENANCE (the key the derived facts are
    # addressed by), not a module output, so a module failing must not erase it.
    #
    # Measured on 2026-08-23: the SDI had 777 ingested dates, 79 current facts and
    # ZERO reporting periods, because ``capital`` failed first on every refresh
    # and took the period with it. Every surface that resolves a date through
    # ``bank_reporting_periods`` then reported "no data yet" — including the
    # Returns workspace, which showed a fully-loaded tenant as having nothing to
    # file. Sample Bank was unaffected only because its official runs
    # (``derive_facts``) create periods on a path that commits.
    session.commit()
    modules_ok, modules_failed = recompute_modules(
        session, ctx, bank, period, reconciliation_block=reconciliation_block
    )
    return RecomputeOutcome(
        period, modules_ok, modules_failed, None, reconciliation_block=reconciliation_block
    )


def run_refresh(session: Session, job: Job) -> None:
    """Cheap-tier worker handler: proactively warm the live view for a bank.

    Delegates to :func:`recompute_live` and records the refresh audit event.
    Idempotent; the live-summary read never invokes this path.
    """
    ctx = _ctx_from_job(session, job)
    bank = _bank_or_error(session, ctx, job)
    as_of = _as_of_from_payload(job)

    outcome = recompute_live(session, ctx, bank, as_of)
    if outcome.skipped_reason is not None or outcome.period is None:
        _mark_prior_live_metrics_unavailable(
            session,
            ctx,
            bank,
            as_of,
            outcome.skipped_reason or "no_period",
        )
        job.progress = {
            "status": "skipped",
            "reason": outcome.skipped_reason or "no_period",
            "as_of_date": as_of.isoformat(),
        }
        return

    progress = {
        "as_of_date": as_of.isoformat(),
        "modules_ok": outcome.modules_ok,
        "modules_failed": outcome.modules_failed,
        "reconciliation_blocked": outcome.reconciliation_block is not None,
    }
    job.progress = progress
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
            "reconciliation_blocked": outcome.reconciliation_block is not None,
        },
    )
    session.commit()
    if outcome.modules_failed:
        raise TransientLiveRefreshError(outcome.modules_failed)


def persist_transient_retry_state(
    session: Session,
    job: Job,
    failure: TransientLiveRefreshError,
) -> None:
    """Mirror the queue row's bounded retry schedule onto failed modules.

    ``job_queue.fail_with_retry`` updates ``attempts`` and ``run_after`` first,
    without committing; this function then stamps the affected live rows in the
    same transaction. A terminally exhausted job has no ``next_retry_at``.
    """
    if job.bank_id is None:
        return
    rows = session.scalars(
        select(LiveMetric).where(
            LiveMetric.organization_id == job.organization_id,
            LiveMetric.bank_id == job.bank_id,
            LiveMetric.module.in_(tuple(failure.modules_failed)),
        )
    )
    for row in rows:
        row.retry_classification = "transient_failure"
        row.retry_attempt_count = job.attempts
        row.next_retry_at = job.run_after if job.status == "queued" else None
    session.flush()


def _mark_prior_live_metrics_unavailable(
    session: Session,
    ctx: TenantContext,
    bank: Bank,
    as_of: date,
    reason: str,
) -> None:
    """Invalidate prior live rows when a newer refresh cannot derive a book.

    An empty first refresh has no row to update and remains a benign no-op. If
    rows already exist, retaining them as ``ready`` would present a result from
    an older financial book after the latest refresh refused to derive one.
    A later input mutation enqueues a new refresh. This structural state is not
    retried on reads or on a timer, so it creates neither a manual-refresh
    workflow nor a permanent retry loop.
    """
    message = (
        f"Live recomputation found no canonical financial book for {as_of.isoformat()} "
        f"({reason}). The prior live result is not current."
    )
    rows = session.scalars(
        select(LiveMetric).where(
            LiveMetric.organization_id == ctx.organization_id,
            LiveMetric.bank_id == bank.id,
        )
    )
    for row in rows:
        row.pipeline_state = "failed"
        row.pipeline_error = message
        row.retry_classification = "structural_unavailable"
        row.retry_attempt_count = 0
        row.next_retry_at = None
    session.commit()


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
        # Facts already exist, so the derivation — and with it the only place
        # the balance-sheet control used to run — is skipped for input_hash
        # stability. That skipped the CONTROL too (audit 2026-08-22 D-3a): a
        # later ingestion at this as-of can break the identity while the facts
        # sit there balanced and stale, and the scheduled run minted immutable
        # runs on them with no verdict anywhere in the chain. The gate is
        # read-only, so reproducibility is untouched.
        period_id = period.id
        filing_reconciliation.assert_filing_reconciled(
            session, ctx, bank, as_of=as_of, period_id=period_id, purpose="official_run"
        )

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
    *,
    reconciliation_block: str | None = None,
) -> None:
    """Upsert one module's live row.

    ``pipeline_state`` is the row's own honesty flag, and it has three values,
    not two: ``ready`` (computed, and the book behind it reconciles),
    ``blocked`` (computed, but the book does NOT reconcile — the metrics are
    kept so the operator can see what they must repair, and ``pipeline_error``
    carries the control's message), and ``failed`` (not computed at all; see
    :func:`_upsert_live_failure`). ``blocked`` is deliberately distinct from
    ``failed``: a failure invites a retry, and re-running the pipeline cannot
    fix a general ledger.
    """
    pipeline_state = "blocked" if reconciliation_block is not None else "ready"
    retry_classification = (
        "structural_unavailable"
        if reconciliation_block is None and metrics.get("availability") == "unavailable"
        else None
    )
    if reconciliation_block is not None:
        metrics = {**metrics, RECONCILIATION_METRIC_KEY: RECONCILIATION_METRIC_BLOCKED}
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
                pipeline_state=pipeline_state,
                pipeline_error=reconciliation_block,
                retry_classification=retry_classification,
                retry_attempt_count=0,
                next_retry_at=None,
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
        existing.pipeline_state = pipeline_state
        existing.pipeline_error = reconciliation_block
        existing.retry_classification = retry_classification
        existing.retry_attempt_count = 0
        existing.next_retry_at = None
        existing.computed_at = now
    _upsert_live_snapshot(session, ctx, bank, period, module, metrics, status, now)
    session.flush()


def _upsert_live_failure(  # noqa: PLR0913 - one upsert carries the full live row
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
                retry_classification="transient_failure",
                retry_attempt_count=0,
                next_retry_at=None,
                computed_at=now,
            )
        )
    else:
        # Live state is keyed by current canonical data. A queued refresh can
        # retain a synthetic ladder period after that period was replaced, so
        # failure provenance must stay period-independent just like success.
        existing.source_fact_period_id = None
        existing.source_as_of_date = period.period_end
        existing.calculation_generation += 1
        existing.pipeline_state = "failed"
        existing.pipeline_error = error
        existing.retry_classification = "transient_failure"
        existing.retry_attempt_count = 0
        existing.next_retry_at = None
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
    period = new_snapshot_period(
        organization_id=ctx.organization_id, bank_id=bank.id, as_of=as_of
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
