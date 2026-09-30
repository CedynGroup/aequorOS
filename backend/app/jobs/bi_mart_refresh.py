"""The ``bi_mart_refresh`` handler: (re)build one bank's marts for one as-of.

Payload (``.ai/bi_contracts.md`` §Interfaces)::

    {"organization_id", "bank_id", "as_of_date", "builder_version", "reason"}

coalesced on ``bi:{bank}:{as_of}`` by every enqueue site, so a burst of
ingestions for one day debounces into one build. The handler itself is thin —
see ``bi_common`` — and the build is
``mart_builder.refresh_bank_as_of(db, *, organization_id, bank_id, as_of, reason)``,
which owns the fingerprint skip, the atomic slice replace, the aggregates,
dimensions and ``bi_dim_date``, and returns a ``BuildOutcome``.

Run gate: ``BI_MART_ENQUEUE_ENABLED`` is re-checked HERE as well as at enqueue
(the ``desk_capture`` idiom). A job queued before the switch was pulled must
not build, and flipping the flag off is therefore also how a backlog drains.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.base import utc_now
from app.jobs import bi_common
from app.models import Job

#: Written to ``payload["reason"]`` by enqueue sites; the handler passes it
#: through to the builder, which records it on ``bi_mart_builds``.
DEFAULT_REASON = "unspecified"


def run_bi_mart_refresh(session: Session, job: Job) -> None:
    """Worker handler: build the marts for ``(bank, as_of)``; idempotent."""
    # Imported at call time, never at module scope: a handler must import
    # cleanly on a worker with no BI feature enabled, and a guard refuses an
    # `app.services.bi` import statement anywhere in `app/jobs/bi_*.py`.
    bi_alerts = bi_common.load_module("app.services.bi.alerts")
    bi_subscriptions = bi_common.load_module("app.services.bi.subscriptions")
    if not get_settings().bi.mart_enqueue_enabled:
        bi_common.mark_skipped(job, bi_common.SKIP_REASON_DISABLED)
        return

    builder = bi_common.load_builder()
    if bi_common.skip_if_payload_is_newer(job, builder.BUILDER_VERSION):
        return

    bank = bi_common.resolve_bank(session, job)
    as_of = bi_common.payload_date(job, "as_of_date")
    reason = str((job.payload or {}).get("reason") or DEFAULT_REASON)

    try:
        outcome = builder.refresh_bank_as_of(
            session,
            organization_id=job.organization_id,
            bank_id=bank.id,
            as_of=as_of,
            reason=reason,
        )
    except bi_common.BiJobError:
        raise
    except Exception as exc:
        raise bi_common.TransientBiBuildError("refresh", exc) from exc

    # A SUCCEEDED build is the only place that knows a bank's figures have
    # actually moved, which is why both of these hang here and not in
    # `refresh_bank_as_of`: `backfill_step` calls the builder once per date, so a
    # hook inside it would mail a bank a thousand board packs while a backfill
    # ran. The builder returns `skipped` when a fingerprint has not moved, so a
    # rebuild that changed nothing enqueues nothing — that is what makes both
    # idempotent without a second mechanism.
    #
    # Without these two calls the features are inert rather than broken, and
    # inert in a way nothing reports: `bi_alert_evaluate` had a job type, a lane
    # and a handler, and no enqueue site anywhere, so no threshold alert had ever
    # been evaluated. Each call checks its own flag first and does not touch the
    # session when it is off.
    alerts_enqueued = 0
    on_new_data_enqueued = 0
    if outcome.status == "succeeded":
        queued_alert = bi_alerts.enqueue_evaluation(
            session, organization_id=job.organization_id, bank_id=bank.id, as_of=as_of
        )
        alerts_enqueued = 1 if queued_alert is not None else 0
        on_new_data_enqueued = len(
            bi_subscriptions.enqueue_on_new_data(
                session,
                organization_id=job.organization_id,
                bank_id=bank.id,
                as_of=as_of,
                # The handler's own "now": `BuildOutcome` carries no timestamp, and
                # this is the moment the build finished. The seam truncates it to
                # the minute to name the run, so second-level precision is not
                # wanted and inventing a field on the builder's contract for it
                # would be the wrong shape.
                completed_at=utc_now(),
            )
        )

    session.commit()
    job.progress = {
        "status": outcome.status,
        "as_of_date": as_of.isoformat(),
        "reason": reason,
        "builder_version": builder.BUILDER_VERSION,
        "fingerprint": outcome.fingerprint,
        "row_counts": dict(outcome.row_counts),
        "alert_evaluations_enqueued": alerts_enqueued,
        "on_new_data_runs_enqueued": on_new_data_enqueued,
    }


__all__ = ["DEFAULT_REASON", "run_bi_mart_refresh"]
