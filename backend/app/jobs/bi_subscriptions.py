"""The two subscription handlers: the hourly scan, and one delivery run.

Both are thin, like every ``app/jobs/bi_*.py``. What decides anything — which
runs are due in the institution's own time zone, each recipient's own
authorization decision, the classification that keeps confidential content out
of an attachment, the render and the relay — lives in
``app.services.bi.subscriptions``.

**Why there are two job types and not one.** ``docs/bi.md`` puts the due-within-
the-hour sweep in ``scheduler.run_tick``, and the tick runs in the CORE worker
lane. Reading ``bi_subscriptions`` there would make the regulatory/live plane
import the BI plane, which ``tests/architecture/test_bi_plane_boundary.py``
forbids and which D-041 exists to prevent: the seam the scheduler may import is
deliberately thin. So the tick enqueues ONE ``bi_subscription_scan`` with nothing
but a literal job type — no BI import at all — and the scan, which runs in the
``bi`` lane where the BI plane belongs, computes the due runs and enqueues each
one with ``run_after`` set to its exact minute. The composite behaviour is what
the spec asks for; the boundary is what the guard asks for (D-181).

**What each handler owns.** The kill-switch re-read at run time, the version
rule, the lazy import, and the ``audit_events`` rows — which
``app/services/bi/*`` may not write (the plane guard), exactly as
``bi_export.py`` owns the audit event for the export service.

**A relay failure is a retry, not a loss.** ``run_subscription`` commits each
recipient's claim before sending and commits the failed row before raising, so a
retry resumes with the recipients this attempt never claimed and never re-sends
one it did. A refused recipient, by contrast, is a ``succeeded`` job: the queue's
retry cannot change an authority decision.
"""

from __future__ import annotations

import importlib
from datetime import timedelta
from types import ModuleType

from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.config import get_settings
from app.db.base import utc_now
from app.jobs import bi_common
from app.models import Job
from app.services import audit

#: The service module both handlers dispatch into. A dotted string, not an
#: import, for the reason ``bi_common.BUILDER_MODULE`` is one.
SUBSCRIPTIONS_MODULE = "app.services.bi.subscriptions"

#: ``audit_events.event_type`` per DELIVERY: one row per recipient, because a
#: delivery is one artifact (or one link) reaching one person, and a refusal at
#: delivery time means authority changed between the schedule and the send —
#: which is exactly what a reviewer looks for.
EVENT_DELIVERED = "bi.subscription.delivered"
EVENT_DENIED = "bi.subscription.denied"

#: ``audit_events.entity_type``: the subscription.
ENTITY_TYPE = "bi_subscription"

#: ``job.progress["reason"]`` when the feature was switched off after the enqueue.
SKIP_REASON_DISABLED = "bi_subscriptions_disabled"


def load_subscriptions() -> ModuleType:
    """Import the subscriptions service at call time. The ONE seam tests stub."""

    return importlib.import_module(SUBSCRIPTIONS_MODULE)


def run_bi_subscription_scan(session: Session, job: Job) -> None:
    """Worker handler: enqueue every clock run due in the coming hour.

    The window starts at the current hour boundary and is one hour long — the
    tick's own cadence — so every minute of every hour is covered exactly once
    even when a scan runs late. A run already queued for a minute is not queued
    again (the service checks the key), and the delivery table's unique
    constraint is the guarantee behind that check.
    """

    if not get_settings().bi.subscriptions_enabled:
        bi_common.mark_skipped(job, SKIP_REASON_DISABLED)
        return

    module = load_subscriptions()
    now = utc_now()
    window_start = now.replace(minute=0, second=0, microsecond=0)
    window_end = window_start + timedelta(hours=1)
    queued = module.enqueue_due_runs(
        session,
        organization_id=job.organization_id,
        window_start=window_start,
        window_end=window_end,
    )
    session.commit()
    job.progress = {
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "runs_enqueued": len(queued),
    }


def run_bi_subscription_run(session: Session, job: Job) -> None:
    """Worker handler: deliver one run, rendering separately for each recipient."""

    if not get_settings().bi.subscriptions_enabled:
        bi_common.mark_skipped(job, SKIP_REASON_DISABLED)
        return

    module = load_subscriptions()
    if bi_common.skip_if_payload_is_newer(job, module.BUILDER_VERSION):
        return

    outcome = module.run_subscription(session, job)
    for delivery in outcome.deliveries:
        if delivery.reason == module.REASON_ALREADY_DELIVERED:
            # Nothing happened on this pass: the original delivery already wrote
            # its own event, and a second one would report two disclosures where
            # there was one.
            continue
        audit.record_event(
            session,
            TenantContext(
                organization_id=job.organization_id,
                actor_user_id=delivery.recipient_user_id,
            ),
            event_type=EVENT_DELIVERED if delivery.status == "sent" else EVENT_DENIED,
            entity_type=ENTITY_TYPE,
            entity_id=outcome.subscription_id,
            details={
                "bank_id": outcome.bank_id,
                "scheduled_for": outcome.scheduled_for.isoformat(),
                "trigger": outcome.trigger,
                **delivery.audit_details(),
            },
        )
    session.commit()
    job.progress = outcome.progress()


__all__ = [
    "ENTITY_TYPE",
    "EVENT_DELIVERED",
    "EVENT_DENIED",
    "SKIP_REASON_DISABLED",
    "SUBSCRIPTIONS_MODULE",
    "load_subscriptions",
    "run_bi_subscription_run",
    "run_bi_subscription_scan",
]
