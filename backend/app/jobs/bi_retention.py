"""The ``bi_retention`` handler: drop daily mart partitions past retention.

Payload: ``{"builder_version"}`` — nothing else, because the action is
PLATFORM-WIDE. The daily fact partitions (``bi_fact_position_daily`` and its
aggregate) are RANGE-partitioned by ``as_of_date`` across every tenant, so
dropping one removes that month for all of them; the month-end table is never
touched (kept forever, D-014). The queue row still carries an
``organization_id`` because every job does; like ``desk_capture`` the job is
carried by whichever tenant's scheduler tick enqueued it and acts globally.

The handler calls
``mart_builder.apply_retention(db, *, retention_days) -> Sequence[str]`` — the
names of the partitions it dropped — with ``BI_DAILY_RETENTION_DAYS`` as the
window. Scheduled by the BI recovery sweep (Phase 1 E2), so its run gate is
``BI_SCHEDULER_ENABLED``, re-checked here so a job queued before the sweep was
switched off does not drop anything.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.jobs import bi_common
from app.models import Job


def run_bi_retention(session: Session, job: Job) -> None:
    """Worker handler: apply the daily-partition retention window once."""
    settings = get_settings().bi
    if not settings.scheduler_enabled:
        bi_common.mark_skipped(job, bi_common.SKIP_REASON_SCHEDULER_DISABLED)
        return

    builder = bi_common.load_builder()
    if bi_common.skip_if_payload_is_newer(job, builder.BUILDER_VERSION):
        return

    try:
        dropped = builder.apply_retention(session, retention_days=settings.daily_retention_days)
    except bi_common.BiJobError:
        raise
    except Exception as exc:
        raise bi_common.TransientBiBuildError("retention", exc) from exc

    session.commit()
    job.progress = {
        "status": "succeeded",
        "retention_days": settings.daily_retention_days,
        "builder_version": builder.BUILDER_VERSION,
        "dropped": [str(name) for name in dropped],
    }


__all__ = ["run_bi_retention"]
