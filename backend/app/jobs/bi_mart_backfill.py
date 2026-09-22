"""The ``bi_mart_backfill`` handler: ONE self-re-enqueuing cursor job.

Payload (``.ai/bi_contracts.md`` §Interfaces)::

    {"organization_id", "bank_id", "cursor_date", "until_date", "builder_version"}

A backfill walks a bank's history newest-first from ``cursor_date`` down to
``until_date``. It is deliberately NOT one job per date and NOT one long job:
each hop calls
``mart_builder.backfill_step(db, *, organization_id, bank_id, cursor, until, budget_seconds)``,
which builds dates for at most ``BI_BACKFILL_HOP_SECONDS`` and returns the NEXT
cursor (or ``None`` when ``until`` has been reached). The handler then commits
and enqueues ITSELF with that cursor on the coalesce key ``bi-backfill:{bank}``
and ``run_after=now``. Because the queue is FIFO within the ``bi`` lane, a
``bi_mart_refresh`` queued by fresh ingestion interleaves at every hop instead
of waiting behind ten years of history, and a crash loses at most one hop.

The reclaim window for this type is derived from the hop bound
(``BiSettings.backfill_stale_after_seconds``): a window shorter than a hop
would reclaim a live hop and build the same dates twice.

**The chain's termination is pinned HERE, not trusted to the builder** (audit
A4-05). A successor is enqueued only for a cursor that is strictly earlier than
this hop's and no earlier than ``until``; anything else — the same date back,
a later date, a date past ``until`` — is a contract violation by the builder
and raises ``BiJobError`` AFTER the hop's own work is committed, so the queue's
bounded retry lands the job ``failed`` where the operator board shows it (a
``succeeded`` row with a "stopped" note in ``progress`` would be invisible
there). Without the guard a non-advancing builder would re-enqueue the same
hop on ``run_after=now`` for ever, each pass burning a full hop budget on the
``bi`` lane; the coalesce key stops parallel chains, not a stuck one.

Operator-triggered only (Phase 1 E2: ``POST /operator/v1/tenants/{org}/bi/backfill``);
no product surface enqueues it. Run gate: ``BI_MART_ENQUEUE_ENABLED`` is
re-checked on every hop, and a hop that finds it off ENDS the chain — the
operator re-triggers after re-enabling, rather than a dormant chain resuming
by surprise.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.base import utc_now
from app.jobs import bi_common
from app.models import Job
from app.services import job_queue

JOB_TYPE = "bi_mart_backfill"


def coalesce_key_for(bank_id: str) -> str:
    """One backfill chain per bank: a second trigger merges into the queued hop."""
    return f"bi-backfill:{bank_id}"


def run_bi_mart_backfill(session: Session, job: Job) -> None:
    """Worker handler: one hop of the backfill, then re-enqueue or finish."""
    if not get_settings().bi.mart_enqueue_enabled:
        bi_common.mark_skipped(job, bi_common.SKIP_REASON_DISABLED)
        return

    builder = bi_common.load_builder()
    if bi_common.skip_if_payload_is_newer(job, builder.BUILDER_VERSION):
        return

    bank = bi_common.resolve_bank(session, job)
    cursor = bi_common.payload_date(job, "cursor_date")
    until = bi_common.payload_date(job, "until_date")
    if until > cursor:
        raise bi_common.BiJobError(
            f"Job payload until_date {until.isoformat()} is after cursor_date "
            f"{cursor.isoformat()}; a backfill walks newest-first."
        )
    budget_seconds = get_settings().bi.backfill_hop_seconds

    try:
        next_cursor: date | None = builder.backfill_step(
            session,
            organization_id=job.organization_id,
            bank_id=bank.id,
            cursor=cursor,
            until=until,
            budget_seconds=budget_seconds,
        )
    except bi_common.BiJobError:
        raise
    except Exception as exc:
        raise bi_common.TransientBiBuildError("backfill", exc) from exc

    # The hop's mart writes are durable BEFORE the next hop exists: a crash
    # between the two leaves finished dates finished and no orphan chain.
    session.commit()

    progress: dict[str, Any] = {
        "cursor_date": cursor.isoformat(),
        "until_date": until.isoformat(),
        "budget_seconds": budget_seconds,
        "builder_version": builder.BUILDER_VERSION,
    }
    if next_cursor is None:
        job.progress = {**progress, "status": "completed"}
        return
    if next_cursor >= cursor or next_cursor < until:
        # Termination is this handler's to guarantee (A4-05): a cursor that does
        # not move strictly earlier, or that overshoots ``until``, must never
        # become a successor hop. The hop's own writes are already committed.
        raise bi_common.BiJobError(
            f"Backfill cursor did not advance: the builder returned "
            f"{next_cursor.isoformat()} for cursor {cursor.isoformat()} and until "
            f"{until.isoformat()}; a hop must return a strictly earlier date no "
            "earlier than until, or None to finish."
        )

    payload = {**(job.payload or {}), "cursor_date": next_cursor.isoformat()}
    successor = job_queue.enqueue(
        session,
        job.organization_id,
        JOB_TYPE,
        bank_id=bank.id,
        payload=payload,
        run_after=utc_now(),
        coalesce_key=coalesce_key_for(bank.id),
        entity_type="bank",
        entity_id=bank.id,
    )
    job.progress = {
        **progress,
        "status": "continued",
        "next_cursor_date": next_cursor.isoformat(),
        "next_job_id": str(successor.id),
    }


__all__ = ["JOB_TYPE", "coalesce_key_for", "run_bi_mart_backfill"]
