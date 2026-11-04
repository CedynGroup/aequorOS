"""The nightly desk capture job: enqueue gating, per-source isolation,
cadence, the Friday auction pass, and determination staging — plus the
JOB_TYPES/HANDLERS parity invariant the repo previously lacked (the
notification_email_mirror orphaned-job-type precedent).

This module's other-suite cases live in ``test_desk_capture_job_integration.py``.
"""

from __future__ import annotations

from app import worker
from app.services import job_queue


def test_every_job_type_has_a_worker_handler() -> None:
    """A job type must ship in JOB_TYPES and worker.HANDLERS together — a
    type without a handler is enqueueable but never claimable, so its jobs
    queue forever (how notification_email_mirror was orphaned until its
    handler entry landed). No documented exceptions."""
    assert set(worker.HANDLERS) == set(job_queue.JOB_TYPES)


def test_notification_email_mirror_is_no_longer_orphaned() -> None:
    """Pin the specific fix: the tick enqueues this type, so the worker must
    claim it."""
    assert "notification_email_mirror" in worker.HANDLERS
