"""The ``bi_nlq_translate`` lifecycle: what is queued, and what the handler writes.

The handler is the only process in the platform that holds a model credential, and
the only thing it is allowed to produce is a PROPOSAL. So the tests here are about
the four ways that could go wrong:

* **it runs after permission was withdrawn.** The AI gates are evaluated again at
  ``phase="run"``, and the BI plane's own two switches are re-read, because the gates
  cannot see them. Each produces a ``cancelled`` record and no model call;
* **it sends something other than what was frozen.** The payload digest is re-checked;
* **a reclaimed job spends a second model call.** A terminal record returns at once;
* **it widens.** A model naming a member the request did not offer produces a refusal,
  and the offending id is recorded on the row for a reviewer and returned to nobody.

Plus the property the whole phase is about: the quota source counts this surface's
requests against the tenant's ONE daily AI budget, so a question and an ICAAP draft
spend from the same number.

This module's other-suite cases live in ``test_nlq_job_integration.py``.
"""

from __future__ import annotations

import pytest

from app.ai.service import quota
from app.bi.jobs import bi_nlq


def test_the_job_type_is_in_the_exclusive_ai_lane() -> None:
    """The process holding the model key runs nothing else, so this type may not be
    claimable by the core worker or by the API's in-process thread."""

    from app.services import job_queue  # noqa: PLC0415 - registry read, not a dependency
    from app.worker import resolve_job_types  # noqa: PLC0415

    assert job_queue.lane_of(bi_nlq.JOB_TYPE) == "ai"
    assert bi_nlq.JOB_TYPE not in resolve_job_types(None)
    assert bi_nlq.JOB_TYPE in resolve_job_types("lane:ai")
    with pytest.raises(Exception, match="exclusive"):
        resolve_job_types("lane:ai,lane:core")


def test_an_organization_is_not_needed_for_the_usage_source_to_be_registered() -> None:
    """The source is registered at import time, so a tenant's budget is one number
    across every AI surface rather than one per surface."""

    assert any(isinstance(source, bi_nlq._NlqUsage) for source in quota.USAGE_SOURCES)
