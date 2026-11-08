"""Durable job queue for the live ALM engine.

A thin service over the ``jobs`` table: ``enqueue`` (with debounce coalescing),
``claim_next`` (``FOR UPDATE SKIP LOCKED`` on Postgres so many workers can poll
one table safely), ``complete``, and ``fail_with_retry`` (exponential backoff up
to ``max_attempts``). ``jobs.job_type`` carries no DB CHECK, so the app-level
allow-list here is the single source of truth for valid types.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from types import MappingProxyType
from typing import Any, cast
from uuid import UUID

from sqlalchemy import or_, select, text
from sqlalchemy.orm import Session

from app.core.authorization import InstitutionScope, Module, Sensitivity
from app.core.tenancy import TenantContext
from app.db.base import utc_now
from app.db.worker_access import (
    CLAIM_SIGNATURE,
    PLATFORM_ENQUEUE_SIGNATURE,
    RECLAIM_SIGNATURE,
    has_worker_function,
)
from app.models import Job


@dataclass(frozen=True)
class JobLocator:
    """The typed source locator a producer must declare for a tenant-facing job.

    ``bank_id`` stays the existing ``enqueue`` parameter and is subsumed by the
    locator: the persisted ``institution_scope`` derives from it, so a producer
    cannot contradict itself. Legacy rows and defined worker/system enqueues
    carry no locator at all (all locator columns NULL) and tenant read paths
    treat every locator-less row as hidden (mission D9).
    """

    module: Module
    sensitivity: Sensitivity


@dataclass(frozen=True)
class JobProvenance:
    """Enqueue provenance (#186): who initiated an enqueue, and under which action.

    ``principal`` is either the initiating principal (``human:<uuid>``,
    ``machine:<uuid>`` or ``operator:<email>``) or an explicit non-tenant
    marker (``WORKER_PRINCIPAL``/``SYSTEM_PRINCIPAL``) for defined worker and
    system enqueues, which tenant users never see.
    """

    principal: str
    action: str
    decision_reference: str | None = None


#: Explicit non-tenant principals for defined worker/system enqueues (D9).
WORKER_PRINCIPAL = "worker"
SYSTEM_PRINCIPAL = "system"
_TENANTLESS_PRINCIPALS = frozenset({WORKER_PRINCIPAL, SYSTEM_PRINCIPAL})


def worker_provenance(action: str, *, decision_reference: str | None = None) -> JobProvenance:
    """Provenance for an enqueue the worker/scheduler initiated itself."""
    return JobProvenance(
        principal=WORKER_PRINCIPAL, action=action, decision_reference=decision_reference
    )


def system_provenance(action: str, *, decision_reference: str | None = None) -> JobProvenance:
    """Provenance for a defined platform/system enqueue with no initiating tenant actor."""
    return JobProvenance(
        principal=SYSTEM_PRINCIPAL, action=action, decision_reference=decision_reference
    )


def operator_provenance(
    operator: str, *, action: str, decision_reference: str | None = None
) -> JobProvenance:
    """Provenance for an enqueue a staff operator initiated (never a tenant identity)."""
    return JobProvenance(
        principal=f"operator:{operator}", action=action, decision_reference=decision_reference
    )


def tenant_principal_marker(ctx: TenantContext) -> str | None:
    """The D9 principal marker a tenant enqueue records, if one applies.

    Integration keys are machine principals; a tenant user is human. Operator
    impersonation and unattributed contexts are staff/system, never a tenant
    identity, so they name no tenant principal.
    """
    if ctx.integration_key_id is not None:
        return f"machine:{ctx.integration_key_id}"
    if ctx.actor_user_id is not None:
        return f"human:{ctx.actor_user_id}"
    return None


def context_provenance(
    ctx: TenantContext, *, action: str, decision_reference: str | None = None
) -> JobProvenance:
    """Attribute an enqueue to the context's initiating principal (D9).

    Integration keys are machine principals; a tenant user is human; operator
    act-as-examiner impersonation is staff, never a tenant identity.
    """
    principal = tenant_principal_marker(ctx)
    if principal is None:
        principal = (
            f"operator:{ctx.actor_operator}"
            if ctx.actor_operator is not None
            else SYSTEM_PRINCIPAL
        )
    return JobProvenance(principal=principal, action=action, decision_reference=decision_reference)


def inherited_provenance(job: Job) -> JobProvenance:
    """The original enqueue provenance for a successor/retry enqueue (D9).

    A successor job inherits its parent's provenance verbatim. A legacy row
    predating the locator columns carries none, so the successor is attributed
    to the worker that produced it rather than being left unattributed.
    """
    return JobProvenance(
        principal=job.enqueued_by_principal or WORKER_PRINCIPAL,
        action=job.enqueued_action or f"retry:{job.job_type}",
        decision_reference=job.enqueue_decision_reference,
    )


def inherited_locator(job: Job) -> JobLocator | None:
    """The original enqueue locator for a successor enqueue, if it carried one."""
    if job.module is None or job.sensitivity is None:
        return None
    return JobLocator(module=Module(job.module), sensitivity=Sensitivity(job.sensitivity))


def _as_aware(value: datetime | None) -> datetime | None:
    """Treat a naive datetime as UTC.

    ``DateTime(timezone=True)`` round-trips as naive on SQLite, so a value read
    back from the DB must be normalized before it is compared to a fresh
    timezone-aware ``run_after``.
    """
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


# Live-engine job types (jobs.job_type has no DB CHECK — validate in code).
#
# HAZARD: a new job type must land in FOUR places in the same change —
# this tuple, ``worker.HANDLERS``, (when the scheduler enqueues it behind
# a flag) ``scheduler.any_scheduling_enabled``, and ``JOB_LANES`` when a
# DEDICATED worker must own it. A type listed here but absent
# from HANDLERS is enqueueable yet never claimable: its jobs sit "queued"
# forever (the notification_email_mirror precedent — enqueued by the tick,
# orphaned until its handler entry landed). The parity test
# ``tests/market_data/service/test_desk_capture_job.py::test_every_job_type_has_a_worker_handler``
# enforces this; keep it green.
JOB_TYPES = (
    "bank_reencrypt",
    "session_purge",
    "document_delete",
    "pipeline_refresh",
    "official_run",
    "scheduled_tick",
    "market_data_pull",
    "temenos_pull",
    "etl_dedup",
    "reporting_deadline_scan",
    "notification_email_mirror",
    "database_direct_health",
    "desk_capture",
    "icaap_ai_draft",
    "bi_commentary",
    "bi_nlq_translate",
    "bi_mart_refresh",
    "bi_mart_backfill",
    "bi_retention",
    "bi_export",
    "bi_alert_evaluate",
    "bi_subscription_scan",
    "bi_subscription_run",
)

#: The lane a worker process must be running to claim a job type.
#:
#: Lanes exist for ONE reason: the AI types are the only jobs that hold an
#: external model credential, and adding them to ``HANDLERS`` (which the parity
#: test above requires) would otherwise make EVERY worker able to claim them —
#: including the API's in-process thread. The default lane never contains an AI
#: type, so the core fleet is unchanged by construction rather than by
#: configuration. ``bi_commentary`` is an AI type for exactly that reason and NOT
#: a BI-lane one: it is the BI surface's job, but the process that runs it is the
#: process holding the model key, and that process must run nothing else.
#:
#: The ``bi`` lane reuses that mechanism for a different reason: mart builds
#: are heavy and the queue is FIFO across every type in a worker's selection,
#: so a BI job in the core selection would sit ahead of a bank's
#: ``pipeline_refresh``. Keeping BI out of the default lane means the core
#: fleet's latency is unchanged by construction, and the API's in-process
#: thread can never run a mart build.
DEFAULT_LANE = "core"
JOB_LANES: Mapping[str, str] = MappingProxyType(
    {
        "icaap_ai_draft": "ai",
        "bi_commentary": "ai",
        "bi_nlq_translate": "ai",
        "bi_mart_refresh": "bi",
        "bi_mart_backfill": "bi",
        "bi_retention": "bi",
        "bi_export": "bi",
        "bi_alert_evaluate": "bi",
        "bi_subscription_scan": "bi",
        "bi_subscription_run": "bi",
    }
)

#: Lanes whose process must run NOTHING else. ``ai`` is exclusive because the
#: process holding the model credential must not host any other handler (a
#: compromise of one handler cannot reach the key). ``bi`` is deliberately NOT
#: exclusive: it is separated for scheduling, not for secrecy, so a developer
#: may run ``lane:core,lane:bi`` in one local process while production pins
#: ``lane:bi`` to its own service (D-007).
EXCLUSIVE_LANES: frozenset[str] = frozenset({"ai"})


def lane_of(job_type: str) -> str:
    return JOB_LANES.get(job_type, DEFAULT_LANE)


def job_types_in_lane(lane: str) -> tuple[str, ...]:
    """Every declared job type belonging to ``lane``, in JOB_TYPES order."""
    return tuple(job_type for job_type in JOB_TYPES if lane_of(job_type) == lane)


# Retry backoff is 2**attempts * base seconds (10s, 20s, 40s at base=5).
_BACKOFF_BASE_SECONDS = 5

#: Per-job-type stale-job windows, in seconds, overriding the deployment-wide
#: ``WORKER_STALE_JOB_SECONDS`` for handlers whose legitimate runtime is far
#: longer than the fleet default.
#:
#: ``reclaim_stale`` requires its window to EXCEED the longest legitimate handler
#: runtime, or a slow-but-alive job is reclaimed and run twice. The default
#: window is 900s; ``etl_dedup`` measurably ran **2h02m** on a 168k-record batch
#: on the primary, so three of its jobs were reclaimed as "worker presumed dead"
#: while still working, one of them twice — three concurrent copies of the same
#: handler. The fix is per-type, not a bigger global number: the global default
#: also governs how quickly a genuinely dead worker's jobs come back, so raising
#: it fleet-wide to accommodate one long handler would slow recovery for the nine
#: short ones.
#:
#: ``etl_dedup``'s 4h is ~2x its worst measured run, with the pairwise
#: counterparty pass now bounded (``etl_dedup_jobs._DEFERRED_COUNTERPARTY_MAX_RECORDS``)
#: so the measured 2h02m is an upper bound, not a typical case. A deployment that
#: raises that bound to get the full pass must raise this window with it.
#:
#: Other jobs use the deployment default, except isolated large-object
#: ``bank_reencrypt`` dispatches; see docs/bank_field_encryption.md for their
#: size-based extension. See ``WorkerSettings.worker_stale_job_seconds``.
STALE_AFTER_OVERRIDES_SECONDS: dict[str, float] = {
    "etl_dedup": 4 * 60 * 60,
    # ``desk_capture`` walks the Bank of Ghana's published tables over HTTP, so
    # its runtime is set by a THIRD PARTY's site and cannot be asserted to fit
    # the fleet default. On 2026-08-23 it did not: the capture ran past the
    # 3600 s window, `reclaim_stale` declared its worker dead, and the retry
    # restarted the walk from the top — three times, for eleven hours, POSTing
    # bog.gov.gh roughly every two seconds. Nothing was wrong with the handler;
    # the reclaim window was simply shorter than the work, which is the exact
    # failure ``etl_dedup`` above records. A reclaim that fires on a live job
    # does not recover it, it duplicates it — and here the duplicate was
    # abusive traffic to the regulator this platform reports to.
    "desk_capture": 6 * 60 * 60,
    # ``bi_export`` renders up to ``BI_EXPORT_ROW_CAP`` rows (100 000) into a
    # PDF or a workbook. Only HALF of that is bounded by a setting: the SQL runs
    # under ``BI_EXPORT_TIMEOUT_MS`` (120 s), and the layout that follows it is
    # not timed at all — reportlab lays out every row of a 100 000-row table.
    # There is no measurement yet, so the window is set well above any plausible
    # render rather than at a number anyone has observed; that is the honest
    # position, and it errs the safe way. A reclaimed export re-renders to the
    # SAME object path and is therefore wasteful rather than corrupting, but it
    # would also write a second ``bi_query_log`` row for one file, which is the
    # reason not to leave it on the fleet default.
    "bi_export": 30 * 60,
    # ``bi_subscription_run`` renders one artifact PER RECIPIENT, up to
    # ``MAX_RECIPIENTS``. Unlike ``bi_export`` its runtime is bounded on BOTH
    # halves: a delivery renders under the INTERACTIVE caps (5 000 rows, a 10 s
    # statement timeout), not the export caps, so the worst case is fifty renders
    # of a five-thousand-row summary. The window is roughly 2.4x that bound, which
    # is the ``etl_dedup`` lesson applied rather than a number picked: a reclaim
    # that fires on a live job does not recover it, and the duplicate here would
    # mail a bank its board pack twice. The alert evaluation and the subscription
    # scan stay on the fleet default, because each is a single bounded pass that
    # ENQUEUES work rather than doing it.
    "bi_subscription_run": 30 * 60,
}

#: ``bi_commentary`` and ``bi_nlq_translate`` take NO entry here on purpose: both are
#: ``ai``-lane types, so
#: ``stale_after_for`` derives its window from the AI settings below — one model
#: call per vendor in the tier plus a margin — which is exactly the bound its
#: runtime has. An entry here would pin a second, competing number.
#:
#: The one BI job whose runtime is a SETTING rather than a measurement:
#: ``bi_mart_backfill`` processes dates for ``BI_BACKFILL_HOP_SECONDS`` and
#: then re-enqueues itself, so its window is derived from that bound in
#: ``BiSettings.backfill_stale_after_seconds`` (the AI-lane shape below) rather
#: than pinned here, where a retuned hop would silently outgrow it.
#: ``bi_mart_refresh`` (one bank, one as-of) and ``bi_retention`` (partition
#: drops) stay on the fleet default and therefore assert they finish inside it;
#: neither has a measurement yet that says otherwise, and a refresh that is
#: reclaimed alive re-runs an idempotent slice replace rather than duplicating
#: a side effect — a real cost, not a corrupting one.
_BACKFILL_JOB_TYPE = "bi_mart_backfill"


def stale_after_for(job_type: str, default: timedelta) -> timedelta:
    """The reclaim window for ``job_type``: its override, else the deployment default.

    The AI lane computes its window from AI settings rather than taking an entry
    in the static override map, because it is derived: the SDK retries timeouts,
    so one model call can legitimately occupy a worker for
    ``AI_REQUEST_TIMEOUT_SECONDS x (AI_MAX_RETRIES + 1)``, and a window shorter
    than that would reclaim a live job and send the request twice. Deriving it
    keeps the two in step when either setting is tuned. The BI backfill hop is
    derived for the same reason, from ``BI_BACKFILL_HOP_SECONDS``.
    """
    override = STALE_AFTER_OVERRIDES_SECONDS.get(job_type)
    if override is not None:
        return timedelta(seconds=override)
    if lane_of(job_type) == "ai":
        from app.core.config import get_settings  # noqa: PLC0415 - avoid an import cycle

        return timedelta(seconds=get_settings().ai.stale_after_seconds)
    if job_type == _BACKFILL_JOB_TYPE:
        from app.core.config import get_settings  # noqa: PLC0415 - avoid an import cycle

        return timedelta(seconds=get_settings().bi.backfill_stale_after_seconds)
    return default


class UnknownJobTypeError(ValueError):
    """A job_type outside the app-level allow-list was requested."""

    def __init__(self, job_type: str) -> None:
        super().__init__(f"Unknown job type {job_type!r}; expected one of {JOB_TYPES}.")
        self.job_type = job_type


def _validate_job_type(job_type: str) -> None:
    if job_type not in JOB_TYPES:
        raise UnknownJobTypeError(job_type)


def backoff(attempts: int) -> timedelta:
    return timedelta(seconds=(2**attempts) * _BACKOFF_BASE_SECONDS)


def enqueue(  # noqa: PLR0913 - queue insert carries the full dispatch envelope
    db: Session,
    organization_id: str,
    job_type: str,
    *,
    bank_id: str | None = None,
    payload: dict[str, Any] | None = None,
    run_after: datetime | None = None,
    coalesce_key: str | None = None,
    entity_type: str | None = None,
    entity_id: UUID | str | None = None,
    max_attempts: int = 3,
    locator: JobLocator | None = None,
    provenance: JobProvenance | None = None,
) -> Job:
    """Insert a queued job, or coalesce into an existing un-started one.

    Every producer must state its ``provenance``; a tenant-initiated enqueue
    (any principal beyond the worker/system markers) must also state a
    ``locator``, which is stamped with the initiating principal onto the row —
    a locator-less job is hidden from every tenant read path (mission D9).
    Omitting ``provenance`` inserts an unattributed row, which tenants never
    see either; the producer census
    (``tests/architecture/test_producer_locator_census.py``) statically refuses
    an app call site that omits either argument.

    When ``coalesce_key`` is given and a still-queued, never-claimed job with the
    same (org, key) AND THE SAME LOCATOR exists, that job's ``run_after`` is
    bumped to the later of the two and payloads are merged — a burst of
    ingestions debounces into one refresh instead of a queue full of
    duplicates. Differing locators never merge: the coalesced row would
    misattribute one caller's work to another's module or bank. Enqueues of
    different tenant-visibility classes never merge either: a tenant-initiated
    enqueue that merged into a worker/system row would hand back an id the
    caller cannot poll. The surviving row keeps its ORIGINAL locator and
    provenance.
    """
    _validate_job_type(job_type)
    payload = payload or {}
    if (
        provenance is not None
        and provenance.principal not in _TENANTLESS_PRINCIPALS
        and locator is None
    ):
        msg = (
            f"job type {job_type!r} is enqueued for principal "
            f"{provenance.principal!r}, which must carry a locator"
        )
        raise ValueError(msg)
    institution_scope: InstitutionScope | None = None
    if locator is not None:
        institution_scope = (
            InstitutionScope.INSTITUTION if bank_id is not None else InstitutionScope.ORGANIZATION
        )
    if coalesce_key is not None:
        wanted = _locator_tuple(
            bank_id=bank_id, institution_scope=institution_scope, locator=locator
        )
        wanted_attributed = _is_tenant_attributed(
            None if provenance is None else provenance.principal
        )
        candidates = db.scalars(
            select(Job)
            .where(
                Job.organization_id == organization_id,
                Job.coalesce_key == coalesce_key,
                Job.status == "queued",
                Job.started_at.is_(None),
            )
            .order_by(Job.queued_at)
        )
        existing = next(
            (
                candidate
                for candidate in candidates
                if _locator_tuple_of(candidate) == wanted
                and _is_tenant_attributed(candidate.enqueued_by_principal) == wanted_attributed
            ),
            None,
        )
        if existing is not None:
            candidates = [
                aware
                for aware in (_as_aware(existing.run_after), _as_aware(run_after))
                if aware is not None
            ]
            existing.run_after = max(candidates) if candidates else None
            existing.payload = {**existing.payload, **payload}
            db.flush()
            return existing

    job = Job(
        organization_id=organization_id,
        job_type=job_type,
        status="queued",
        entity_type=entity_type,
        entity_id=None if entity_id is None else str(entity_id),
        bank_id=bank_id,
        payload=payload,
        run_after=run_after,
        coalesce_key=coalesce_key,
        max_attempts=max_attempts,
        institution_scope=None if institution_scope is None else institution_scope.value,
        module=None if locator is None else locator.module.value,
        sensitivity=None if locator is None else locator.sensitivity.value,
        enqueued_by_principal=None if provenance is None else provenance.principal,
        enqueued_action=None if provenance is None else provenance.action,
        enqueue_decision_reference=(None if provenance is None else provenance.decision_reference),
    )
    db.add(job)
    db.flush()
    return job


def _locator_tuple(
    *,
    bank_id: str | None,
    institution_scope: InstitutionScope | None,
    locator: JobLocator | None,
) -> tuple[str | None, str | None, str | None, str | None]:
    """The locator an enqueue would match for coalescing (or stamp on a row).

    ``bank_id`` participates even without a module: it is part of the persisted
    locator (the migration subsumes it), so a bankless enqueue never merges into
    a bank-targeted row and two different banks never merge, whatever else.
    """
    if locator is None:
        return (None, bank_id, None, None)
    return (
        institution_scope.value if institution_scope is not None else None,
        bank_id,
        locator.module.value,
        locator.sensitivity.value,
    )


def _locator_tuple_of(job: Job) -> tuple[str | None, str | None, str | None, str | None]:
    return (job.institution_scope, job.bank_id, job.module, job.sensitivity)


def _is_tenant_attributed(principal: str | None) -> bool:
    """Mirror the ``human:``/``machine:`` test in ``jobs.tenant_visibility_condition``."""
    return principal is not None and principal.startswith(("human:", "machine:"))


def enqueue_platform_daily(
    db: Session, organization_id: str, job_type: str, day: date, payload: dict[str, object]
) -> Job | None:
    """Preserve the platform cadence without granting cross-tenant queue reads."""
    if job_type not in ("desk_capture", "bi_retention"):
        raise ValueError("Only platform daily job kinds may use this boundary")
    if has_worker_function(db, PLATFORM_ENQUEUE_SIGNATURE):
        identifier = cast(
            UUID | None,
            db.scalar(
                text(
                    "SELECT aqs_worker_enqueue_platform_daily(:kind, :day, CAST(:payload AS jsonb))"
                ),
                {"kind": job_type, "day": day, "payload": json.dumps(payload)},
            ),
        )
        return None if identifier is None else db.get(Job, identifier)
    prefix = "desk_capture" if job_type == "desk_capture" else "bi-retention"
    key = f"{prefix}:{day.isoformat()}"
    if db.scalar(select(Job.id).where(Job.job_type == job_type, Job.coalesce_key == key).limit(1)):
        return None
    return enqueue(
        db,
        organization_id,
        job_type,
        payload=payload,
        coalesce_key=key,
        locator=None,
        provenance=worker_provenance(f"platform.daily:{job_type}"),
    )


def seed_tick(db: Session, organization_id: str) -> Job:
    """Seed the first (or a replacement) tick for one org; coalesces to one."""
    return enqueue(
        db,
        organization_id,
        "scheduled_tick",
        run_after=utc_now(),
        coalesce_key=f"tick:{organization_id}",
        locator=None,
        provenance=worker_provenance("scheduler.seed_tick"),
    )


def claim_next(
    db: Session,
    now: datetime,
    job_types: tuple[str, ...],
    *,
    claimed_by: str | None = None,
) -> Job | None:
    """Claim the oldest due, queued job of one of ``job_types``.

    Uses ``FOR UPDATE SKIP LOCKED`` on Postgres so concurrent workers never
    claim the same row; the claim (status → running) is committed before return.
    ``claimed_by`` identifies the worker runtime for operator incident review.
    """
    for job_type in job_types:
        _validate_job_type(job_type)
    if has_worker_function(db, CLAIM_SIGNATURE):
        envelope = cast(
            Job | None,
            db.scalar(
                select(Job).from_statement(
                    text(
                        "SELECT * FROM aqs_worker_claim_job(:now, "
                        "CAST(:job_types AS text[]), :claimed_by)"
                    )
                ),
                {"now": now, "job_types": list(job_types), "claimed_by": claimed_by},
            ),
        )
        if envelope is not None:
            # Claim envelopes must not reload under an unscoped RLS session.
            db.expunge(envelope)
        db.commit()
        return envelope
    stmt = (
        select(Job)
        .where(
            Job.status == "queued",
            or_(Job.run_after.is_(None), Job.run_after <= now),
            Job.job_type.in_(job_types),
        )
        .order_by(Job.queued_at)
        .limit(1)
    )
    if db.get_bind().dialect.name == "postgresql":
        stmt = stmt.with_for_update(skip_locked=True)
    job = db.scalar(stmt)
    if job is None:
        return None
    job.status = "running"
    job.claimed_by = claimed_by
    job.started_at = now
    db.commit()
    return job


def complete(db: Session, job: Job, progress: dict[str, Any] | None = None) -> Job:
    job.status = "succeeded"
    # Clear any interim error (e.g. a reaper "reclaimed" note written while this
    # slow-but-alive handler was still running) — a succeeded job must not read
    # as failed in the queue history.
    job.error = None
    job.completed_at = utc_now()
    if progress is not None:
        job.progress = progress
    db.commit()
    return job


def fail_with_retry(
    db: Session,
    job: Job,
    error: str,
    *,
    now: datetime | None = None,
    commit: bool = True,
) -> Job:
    """Requeue the job with exponential backoff, or fail it past max attempts."""
    now = now or utc_now()
    job.error = error
    if job.attempts < job.max_attempts:
        job.attempts += 1
        job.status = "queued"
        job.started_at = None
        job.run_after = now + backoff(job.attempts)
    else:
        job.status = "failed"
        job.completed_at = now
    if commit:
        db.commit()
    else:
        db.flush()
    return job


def is_exhausted(job: Job) -> bool:
    """Whether the queue will never touch this job again.

    Terminal (``failed``) with every attempt used. A ``failed`` job with retries
    left does not exist — ``fail_with_retry`` requeues until ``max_attempts`` —
    so this is the precise "needs a human" state, distinct from "will retry
    shortly", which is what made four stranded batches on the primary
    indistinguishable from four pending ones.
    """
    return job.status == "failed" and job.attempts >= job.max_attempts


def latest_for_entity(
    db: Session,
    *,
    organization_id: str,
    job_type: str,
    entity_id: UUID | str,
) -> Job | None:
    """The most recently queued job of ``job_type`` for one entity, org-scoped.

    Explicitly organization-scoped: callers include the operator control plane,
    whose session bypasses RLS, so the filter here is the isolation.
    """
    _validate_job_type(job_type)
    return db.scalar(
        select(Job)
        .where(
            Job.organization_id == organization_id,
            Job.job_type == job_type,
            Job.entity_id == str(entity_id),
        )
        .order_by(Job.queued_at.desc())
        .limit(1)
    )


def reclaim_stale(
    db: Session,
    now: datetime,
    *,
    stale_after: timedelta,
    job_types: tuple[str, ...] | None = None,
) -> int:
    """Reclaim jobs stuck in ``running`` past ``stale_after`` and return the count.

    ``claim_next`` commits ``status='running'`` before the handler runs, and only
    ``complete``/``fail_with_retry`` move a job out of ``running``. A worker that
    dies *between* those points — SIGKILL/SIGTERM mid-handler, OOM, or a severed
    DB connection — leaves the row ``running`` forever, because a raising handler
    is the only failure the poll loop catches. Nothing else resets it.

    This reaper treats such a row as a used attempt (so a job that reliably kills
    its worker eventually lands in ``failed`` for investigation instead of being
    reclaimed forever) and otherwise requeues it for immediate re-dispatch.

    ``stale_after`` must exceed the longest legitimate handler runtime, or a
    slow-but-alive job will be reclaimed and run twice. That requirement is now
    met PER JOB TYPE: ``stale_after`` is the deployment-wide default and
    :data:`STALE_AFTER_OVERRIDES_SECONDS` widens it for the handlers that
    genuinely run for hours. Before that, ``etl_dedup`` (measured 2h02m) was
    reclaimed against the 900s default while still running — the reaper requeued
    one job twice, so three copies of the same handler ran concurrently, and the
    batch ended ``failed`` with "worker presumed dead" despite nothing having
    died.
    """
    if has_worker_function(db, RECLAIM_SIGNATURE):
        selected = JOB_TYPES if job_types is None else job_types
        for job_type in selected:
            _validate_job_type(job_type)
        windows = {
            job_type: stale_after_for(job_type, stale_after).total_seconds()
            for job_type in selected
        }
        count = cast(
            int,
            db.scalar(
                text("SELECT aqs_worker_reclaim_jobs(:now, CAST(:windows AS jsonb))"),
                {"now": now, "windows": json.dumps(windows)},
            ),
        )
        db.commit()
        return int(count or 0)
    statement = select(Job).where(Job.status == "running", Job.completed_at.is_(None))
    if job_types is not None:
        # Each worker reaps only its OWN lane, so a core worker can never apply
        # the fleet default to an AI job whose legitimate runtime is minutes.
        statement = statement.where(Job.job_type.in_(job_types))
    running = db.scalars(statement).all()
    reclaimed = 0
    for job in running:
        window = stale_after_for(job.job_type, stale_after)
        if job.job_type == "bank_reencrypt":
            size = cast(object, job.payload.get("reencryption_bytes"))
            if type(size) is int and size > 0:
                window += timedelta(seconds=8 * size / (1024 * 1024))
        started = _as_aware(job.started_at)
        if started is None or started >= now - window:
            continue
        job.error = (
            f"reclaimed: running since {started.isoformat()} exceeded {window} "
            "without completing (worker presumed dead)"
        )
        if job.attempts < job.max_attempts:
            job.attempts += 1
            job.status = "queued"
            job.started_at = None
            job.run_after = now
        else:
            job.status = "failed"
            job.completed_at = now
        reclaimed += 1
    if reclaimed:
        db.commit()
    return reclaimed
