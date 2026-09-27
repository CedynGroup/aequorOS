"""The ``bi_alert_evaluate`` handler: judge one bank's alerts, in the ``bi`` lane.

Thin, like its siblings (``app/jobs/bi_*.py``). Everything that decides anything
— re-authorizing the owner, resolving the threshold, reading the figure, working
out whether the state changed and who may be told — lives in
``app.services.bi.alerts``. What this module owns is the four things a handler
owns, plus the one write the service may not make:

1. **the run-time kill-switch.** ``BI_ALERTS_ENABLED`` is re-read HERE, not
   trusted from the enqueue: a job queued before the flag went off must not
   notify a bank after it (the ``bi_mart_refresh`` idiom), and flipping the flag
   off is therefore also how a backlog drains;
2. **the version rule (P1-B5).** A handler OLDER than the payload's
   ``builder_version`` skips: the mart shape it would read is not the one the
   enqueuer described;
3. **the lazy import.** ``app/jobs`` must import cleanly on a worker with no BI
   service layer, because the queue registration ships one release BEFORE the
   feature is enabled (D-008);
4. **the audit event**, for the same reason ``bi_export`` writes its own: the
   plane guard admits ``bi_*`` writes from ``app/services/bi`` and nothing else;
5. **the ``notifications`` rows.** Same rule, and it is the point of the split —
   the service says WHO may be told and what the sentence is, and the handler is
   the only place that may write the row that tells them. The event row's
   ``notified_user_ids`` is stamped in the SAME transaction as those rows, so an
   event can never claim a notification that does not exist.

A job that evaluates nothing — no alerts, no build for the date — is a
``succeeded`` job whose progress says so. Retrying could not change either fact.
"""

from __future__ import annotations

import importlib
from types import ModuleType

from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.config import get_settings
from app.jobs import bi_common
from app.models import Job
from app.services import audit, notifications

#: The service module this handler dispatches into. A dotted string, not an
#: import, for the reason ``bi_common.BUILDER_MODULE`` is one.
ALERTS_MODULE = "app.services.bi.alerts"

#: ``audit_events.event_type`` for a recorded transition. Both directions are
#: audited: a threshold clearing is as much a governance fact as reaching one.
EVENT_BREACHED = "bi.alert.breached"
EVENT_CLEARED = "bi.alert.cleared"

#: ``audit_events.entity_type``: the alert, because that is what a reviewer
#: follows and what the owner's history is keyed by.
ENTITY_TYPE = "bi_alert"

#: ``job.progress["reason"]`` when the feature was switched off after the enqueue.
SKIP_REASON_DISABLED = "bi_alerts_disabled"


def load_alerts() -> ModuleType:
    """Import the alerts service at call time. The ONE seam tests stub."""

    return importlib.import_module(ALERTS_MODULE)


def run_bi_alert_evaluate(session: Session, job: Job) -> None:
    """Worker handler: evaluate one bank's alerts for one as-of, and notify."""

    if not get_settings().bi.alerts_enabled:
        bi_common.mark_skipped(job, SKIP_REASON_DISABLED)
        return

    module = load_alerts()
    if bi_common.skip_if_payload_is_newer(job, module.BUILDER_VERSION):
        return

    bank = bi_common.resolve_bank(session, job)
    as_of = bi_common.payload_date(job, "as_of_date")

    evaluation = module.evaluate_bank(
        session,
        organization_id=job.organization_id,
        bank_id=bank.id,
        as_of=as_of,
    )
    notified = 0
    for outcome in evaluation.transitions:
        copy = outcome.notification
        if copy is None:  # pragma: no cover - ``transitions`` filters on it
            continue
        for recipient_id in outcome.admitted_user_ids:
            notifications.emit(
                session,
                TenantContext(organization_id=job.organization_id, actor_user_id=recipient_id),
                type=copy.type,
                severity=copy.severity,
                title=copy.title,
                body=copy.body,
                entity_type=ENTITY_TYPE,
                entity_id=outcome.alert_id,
                recipient_user_id=recipient_id,
            )
            notified += 1
        if outcome.event is not None:
            outcome.event.notified_user_ids = [
                str(user_id) for user_id in outcome.admitted_user_ids
            ]
        audit.record_event(
            session,
            TenantContext(organization_id=job.organization_id),
            event_type=EVENT_BREACHED if outcome.result == "breached" else EVENT_CLEARED,
            entity_type=ENTITY_TYPE,
            entity_id=outcome.alert_id,
            details={
                "bank_id": bank.id,
                "as_of_date": as_of.isoformat(),
                "measure_id": outcome.measure_id,
                "threshold_basis": (
                    None if outcome.event is None else outcome.event.threshold_basis
                ),
                "build_fingerprint": evaluation.build_fingerprint,
                "notified_user_ids": [str(user) for user in outcome.admitted_user_ids],
                "withheld_user_ids": [str(user) for user in outcome.withheld_user_ids],
            },
        )
    session.commit()
    job.progress = {**evaluation.progress(), "notifications_emitted": notified}


__all__ = [
    "ALERTS_MODULE",
    "ENTITY_TYPE",
    "EVENT_BREACHED",
    "EVENT_CLEARED",
    "SKIP_REASON_DISABLED",
    "load_alerts",
    "run_bi_alert_evaluate",
]
