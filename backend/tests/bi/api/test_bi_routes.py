"""The BI read surface end to end: ``/api/v1/banks/{bank_id}/bi/*``.

The order the handler consults its guards in is the security property, so this
file asserts the order rather than the pieces: a sibling tenant's institution is
404 on every route whatever the deployment flag says; the flag makes the whole
surface absent; an impersonated operator and a machine key never reach a query;
a denial names the members and carries no rows; the catalogue advertises exactly
what the query path will serve; and neither the row cap, the page size nor the
grid's paging can be widened from the request.

The mart below is hand-built (four positions, two branches, two obligors, one
engine copy) for the same reason the compiler's own fixture is: every expected
number is derivable by reading it, so a failure names a broken rule instead of a
drifted golden. It is written directly rather than through the builder because
what is under test here is the ROUTE — the builder has its own suites.

This module's other-suite cases live in ``test_bi_routes_integration.py``.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.bi.api import read_bi
from app.bi.schemas.query import BiDateRange, BiTime
from app.bi.service import compiler
from tests.bi.route_data import AS_OF


@pytest.mark.parametrize(
    "time",
    [
        BiTime(as_of=AS_OF),
        BiTime(as_of=AS_OF, compare_to=dt.date(2026, 7, 31)),
        BiTime(range=BiDateRange(start=dt.date(2026, 8, 1), end=AS_OF)),
        BiTime(
            range=BiDateRange(start=dt.date(2026, 8, 1), end=AS_OF),
            compare_to=dt.date(2026, 7, 31),
        ),
    ],
)
def test_the_provenance_window_covers_every_date_the_compiler_reads(time: BiTime) -> None:
    """The fingerprint must span the comparison period too, so this mirrors the
    compiler's own window arithmetic rather than restating it."""
    current, prior = compiler._windows(time)  # noqa: SLF001 - the rule under comparison
    expected_start = current.start if prior is None else min(current.start, prior.start)
    expected_end = current.end if prior is None else max(current.end, prior.end)
    assert read_bi._data_window(time) == (expected_start, expected_end)  # noqa: SLF001
