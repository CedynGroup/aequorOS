"""Scheduler: due official runs enqueued + self-perpetuating tick, inert by default.

This module's other-suite cases live in ``test_scheduler_integration.py``.
"""

from __future__ import annotations

from app.bi.service import enqueue as bi_enqueue


def test_the_recovery_bound_mirrors_the_queues_own_max_attempts() -> None:
    """The number is not free-floating: three terminal failures is the queue's
    own "stop treating this as transient" contract applied one level up."""
    assert bi_enqueue.MAX_RECOVERY_FAILURES == 3
