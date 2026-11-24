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


def test_notification_dispatch_is_claimable() -> None:
    """Pin the specific fix, carried over when the SMTP mirror became the
    notification dispatcher: publishing enqueues this type, so the worker must
    claim it — and the retired mirror type must be gone from both tables."""
    assert "notification_dispatch" in worker.HANDLERS
    assert "notification_email_mirror" not in job_queue.JOB_TYPES
