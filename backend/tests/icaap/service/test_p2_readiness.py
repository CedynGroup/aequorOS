"""Readiness after P2: the assessment behind the document, not just the text.

P1 answered "is it written and are its figures current". These are the rules
that answer "is the assessment finished" — and they land in the SAME list, so a
preparer sees one checklist and P3's freeze reads one answer.

This module's other-suite cases live in ``test_p2_readiness_integration.py``.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.icaap.service import readiness_p2
from tests.icaap.service.test_p2_readiness_integration import TODAY


@pytest.mark.parametrize(
    ("performed", "expected"),
    [(date(2025, 1, 1), True), (date(2025, 3, 1), False)],
)
def test_a_review_older_than_the_governed_period_is_reported_as_outdated(
    performed: date, expected: bool
) -> None:
    assert readiness_p2._months_old(performed, TODAY, 12) is expected  # noqa: SLF001
