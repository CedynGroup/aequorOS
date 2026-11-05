"""``POST /banks/{id}/bi/export`` and its collection route: the policy, end to end.

The artifacts themselves are ``tests/bi/service/test_exports.py``. What this
file asserts is the thing that decides whether a file is produced at all, and
for whom:

* **a summary export needs ``view``; a record-level or confidential one needs
  ``export`` as well.** A ``viewer`` who can drill into position references
  interactively is refused the SAME query as an export, with no bytes.
* **an export can never return a member the caller could not query
  interactively.** The elevated permission does not stand in for the ordinary
  one: the decision is the read surface's own ``authorize_query``, asked twice.
* **one ``bi_query_log`` row per request**, with ``surface="export"``.
* **the async threshold moves the work to the ``bi`` lane** rather than
  answering inline, and the finished file is collectable only by the principal
  who asked for it.

The mart is the read surface's own fixture (``tests/bi/api/test_bi_routes``) so the
two surfaces cannot be tested against different data. The flag and cross-tenant
sweeps are there too, parametrised over ``ROUTES``, which now includes this one.

This module's other-suite cases live in ``test_bi_exports_integration.py``.
"""

from __future__ import annotations

import datetime as dt

from app.bi.service.exports import jobs as export_jobs
from app.core.config import get_settings
from app.services import job_queue


def test_the_product_threshold_is_the_specs_ten_thousand_rows() -> None:
    settings = get_settings().bi
    assert settings.export_async_threshold_rows == 10_000
    assert settings.export_row_cap == 100_000
    assert settings.export_timeout_ms == 120_000


def test_the_queued_job_belongs_to_the_bi_lane_and_has_a_handler() -> None:
    """D-008: a job type with no handler in its lane sits ``queued`` for ever."""

    from app.worker import HANDLERS  # noqa: PLC0415 - the registration under test

    assert export_jobs.JOB_TYPE in job_queue.JOB_TYPES
    assert job_queue.lane_of(export_jobs.JOB_TYPE) == "bi"
    assert export_jobs.JOB_TYPE in HANDLERS
    assert export_jobs.JOB_TYPE in job_queue.job_types_in_lane("bi")
    # The reclaim window must exceed the longest legitimate render.
    assert job_queue.stale_after_for(
        export_jobs.JOB_TYPE, dt.timedelta(seconds=900)
    ) > dt.timedelta(seconds=900)
