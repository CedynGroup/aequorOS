"""The ONE enqueue seam for ``bi_mart_refresh`` (BI Phase 1, wave 2).

Every product hook that can change what a mart would show — an accepted
ingestion batch, an approved or reversed withdrawal, a completed live or
official pipeline run, and every register/entitlement/parameter trigger that
reflows the live plane — calls :func:`enqueue_mart_refresh` and nothing else.
Centralising it means the kill-switch, the coalesce key, the payload shape and
the version stamp are decided once, and a hook site cannot drift from the
handler's contract (``app/jobs/bi_mart_refresh.py``).

It also owns the READ half of the scheduler's recovery sweep
(:func:`banks_due_for_rebuild`), so the regulatory/live plane imports this
module and ``versions`` and no other BI module at all (D-041, D-043).

Three rules, all structural:

* **Default off, and inert when off.** ``BI_MART_ENQUEUE_ENABLED`` is checked
  FIRST; when it is off the function returns ``None`` before touching the
  session, so a deployment without ``risk-worker-bi`` enqueues nothing it could
  never claim (D-008, the ``notification_email_mirror`` orphan). The handler
  re-checks the same flag at run time.
* **No builder on the hot path.** This module imports the version constant
  from ``app.services.bi.versions`` — a module with no imports — and, for the
  due-check, the two BI MODELS it reads. It never imports the mart builder, the
  catalogue, the compiler or ``app.jobs`` (AST-pinned). The models are free:
  every consumer of ``app.models`` already loads ``app.models.bi`` through that
  package's ``__init__``. Ingestion and the pipelines run in the request path
  and the core worker; the builder belongs to the ``bi`` lane alone.
* **One job per ``(bank, as_of)``.** Coalesce key ``bi:{bank}:{as_of}``
  (prefixed, because ``job_queue.enqueue`` coalesces on ``(org, key)`` with no
  job-type dimension, so a bare key would merge with ``refresh:`` rows). A burst
  of uploads for one day, or an ingestion followed by the live refresh it
  triggers, debounces into a single build; ``run_after`` rides the same
  ``PIPELINE_DEBOUNCE_SECONDS`` window as ``pipeline_refresh`` so the two
  settle together.

The ``as_of`` a hook passes is the hook's decision, documented at each site:
an ingestion batch passes its OWN ``as_of_date`` (that is the slice whose
inputs changed); the live-plane triggers pass the bank's LIVE date
(``max(CurrentFinancialFact.source_as_of_date)``, the date whose engine
metrics will move); a withdrawal passes the withdrawn book's date (its slice
is what changed, and a per-date slice replace has no live-plane rollback
hazard — D-025's concern does not apply to a mart); the pipelines pass the
date they just computed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.base import utc_now
from app.identity.public import Bank
from app.models import BiMartBuild, CurrentFinancialFact, Job
from app.models.bi import MART_BUILD_SCOPES
from app.services import job_queue
from app.services.bi.versions import BUILDER_VERSION

#: The job type this seam enqueues. Literal here (not imported from the
#: handler) so the enqueue side never imports ``app.jobs``; ``job_queue.enqueue``
#: validates it against ``JOB_TYPES``.
JOB_TYPE = "bi_mart_refresh"

#: ``jobs.entity_type`` for a mart refresh: the bank, so the operator board and
#: ``job_queue.latest_for_entity`` can find a bank's latest build request.
ENTITY_TYPE = "bank"


def coalesce_key_for(bank_id: str, as_of: date) -> str:
    """``bi:{bank}:{as_of}`` — one queued build per bank and day."""
    return f"bi:{bank_id}:{as_of.isoformat()}"


def enqueue_mart_refresh(
    db: Session,
    *,
    organization_id: str,
    bank_id: str,
    as_of: date,
    reason: str,
) -> Job | None:
    """Enqueue one coalesced ``bi_mart_refresh`` for ``(bank, as_of)``.

    Returns the queued job (new, or the un-started one this call merged into),
    or ``None`` when ``BI_MART_ENQUEUE_ENABLED`` is off — in which case the
    session is not touched at all. Flushes but never commits: the hook's own
    transaction owns the commit, so the job lands (or rolls back) with the
    mutation that asked for it.
    """
    settings = get_settings()
    if not settings.bi.mart_enqueue_enabled:
        return None
    debounce = settings.worker.pipeline_debounce_seconds
    return job_queue.enqueue(
        db,
        organization_id,
        JOB_TYPE,
        bank_id=bank_id,
        payload={
            "organization_id": organization_id,
            "bank_id": bank_id,
            "as_of_date": as_of.isoformat(),
            "builder_version": BUILDER_VERSION,
            "reason": reason,
        },
        run_after=utc_now() + timedelta(seconds=debounce),
        coalesce_key=coalesce_key_for(bank_id, as_of),
        entity_type=ENTITY_TYPE,
        entity_id=bank_id,
    )


#: How many consecutive TERMINALLY failed refresh jobs stop the recovery sweep
#: re-offering one ``(bank, as_of)``.
#:
#: The precedent is the queue's own ``max_attempts`` (3): a job that has used
#: every attempt is ``failed`` and nothing re-enqueues it, because three tries
#: is where this codebase stops treating a failure as transient. The sweep sits
#: one level above that, so the same number of TERMINAL failures — three jobs,
#: nine handler attempts, spanning at least three hourly ticks — is where a
#: transient cause (a severed connection, a reclaimed live job, a vendor
#: hiccup) has been ruled out and the build is deterministically broken: bad
#: data or a code defect, neither of which another tick can fix.
#:
#: Without the bound the sweep re-enqueued such a bank EVERY HOUR for ever,
#: each pass burning a full ``max_attempts`` chain on the ``bi`` lane, with no
#: operator signal (``GET /operator/v1/jobs`` does not surface ``progress``).
#: Recovery is deliberately manual after that, exactly as a stranded
#: ``etl_dedup`` pass is: an operator reads the recorded error and re-triggers
#: through ``POST /operator/v1/tenants/{org}/bi/backfill``, which enqueues a
#: ``bi_mart_backfill`` on its own key and is therefore never blocked by this
#: bound. A later success resets the count by construction — the newest job on
#: the key is then no longer ``failed``.
MAX_RECOVERY_FAILURES = 3


@dataclass(frozen=True)
class DueRebuild:
    """One ``(bank, as_of)`` the recovery sweep should rebuild."""

    organization_id: str
    bank_id: str
    as_of: date


def _live_as_of(db: Session, organization_id: str, bank_id: str) -> date | None:
    """The bank's live input generation — the date every BI trigger keys on."""
    return db.scalar(
        select(func.max(CurrentFinancialFact.source_as_of_date)).where(
            CurrentFinancialFact.organization_id == organization_id,
            CurrentFinancialFact.bank_id == bank_id,
        )
    )


def _is_fully_built(db: Session, organization_id: str, bank_id: str, as_of: date) -> bool:
    """Whether every build scope for ``(bank, as_of)`` stands ``succeeded``.

    ``succeeded`` is the ONLY status that counts as built, and it is also what
    terminates the sweep after a fingerprint skip: ``refresh_bank_as_of``
    returns BEFORE it touches ``bi_mart_builds`` when the fingerprint has not
    moved, so an unchanged slice keeps the ``succeeded`` rows of the build that
    did the work. No writer ever stores ``skipped`` — that word is a job
    ``progress`` fact, never a build status (``app/jobs/bi_common.py``).
    """
    built = set(
        db.scalars(
            select(BiMartBuild.scope).where(
                BiMartBuild.organization_id == organization_id,
                BiMartBuild.bank_id == bank_id,
                BiMartBuild.as_of_date == as_of,
                BiMartBuild.status == "succeeded",
            )
        )
    )
    return built >= set(MART_BUILD_SCOPES)


def _recovery_is_exhausted(db: Session, organization_id: str, bank_id: str, as_of: date) -> bool:
    """Whether this slice has failed terminally :data:`MAX_RECOVERY_FAILURES` times.

    Counted from the ``jobs`` history on the slice's coalesce key, NOT from
    ``bi_mart_builds``: the builder reuses one row per ``(bank, as_of, scope)``
    and resets it to ``running`` on every attempt, so the build table holds the
    LATEST outcome and no history at all. The queue keeps every terminal row,
    and the key ``bi:{bank}:{as_of}`` names the slice exactly, so the newest
    jobs on that key are this slice's attempt history.

    Consecutive by construction: only the newest
    :data:`MAX_RECOVERY_FAILURES` rows are read, so one ``succeeded`` (or a
    live ``queued`` / ``running``) row among them means not exhausted.
    """
    recent = list(
        db.scalars(
            select(Job.status)
            .where(
                Job.organization_id == organization_id,
                Job.job_type == JOB_TYPE,
                Job.coalesce_key == coalesce_key_for(bank_id, as_of),
            )
            .order_by(Job.queued_at.desc())
            .limit(MAX_RECOVERY_FAILURES)
        )
    )
    return len(recent) >= MAX_RECOVERY_FAILURES and all(status == "failed" for status in recent)


def banks_due_for_rebuild(db: Session, *, organization_id: str) -> list[DueRebuild]:
    """Every ``(bank, live as-of)`` of one tenant whose marts are not built.

    The read half of the scheduler's recovery sweep, and it lives HERE rather
    than in ``scheduler.py`` so the regulatory/live plane imports only this
    seam and ``versions`` — the BI models are read inside the BI package
    (D-041, D-043). Importing them costs the hot path nothing: every consumer
    of ``app.models`` already loads ``app.models.bi`` through its package
    ``__init__``.

    A slice is due when its live date lacks a ``succeeded`` build row for ANY
    scope — missing (the authoritative hook never fired), ``failed`` (the build
    raised past its attempts) or ``running`` (a crashed builder never closed
    it) — UNLESS it has already failed terminally
    :data:`MAX_RECOVERY_FAILURES` times, which is where the sweep stops and an
    operator's backfill takes over.

    Pure read: it enqueues nothing. The caller owns the flag check and the
    enqueue, so a disabled deployment does not even ask.
    """
    due: list[DueRebuild] = []
    banks = list(db.scalars(select(Bank).where(Bank.organization_id == organization_id)))
    for bank in banks:
        as_of = _live_as_of(db, organization_id, bank.id)
        if as_of is None:
            continue
        if _is_fully_built(db, organization_id, bank.id, as_of):
            continue
        if _recovery_is_exhausted(db, organization_id, bank.id, as_of):
            continue
        due.append(DueRebuild(organization_id=organization_id, bank_id=bank.id, as_of=as_of))
    return due


__all__ = [
    "ENTITY_TYPE",
    "JOB_TYPE",
    "MAX_RECOVERY_FAILURES",
    "DueRebuild",
    "banks_due_for_rebuild",
    "coalesce_key_for",
    "enqueue_mart_refresh",
]
