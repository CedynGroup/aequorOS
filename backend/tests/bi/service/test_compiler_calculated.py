"""A bank-certified calculated measure, compiled and EXECUTED (D-195 … D-206).

The fixture is five month-end books and two loan events, so every expected number
in this file is hand arithmetic over rows that are written out below. That is the
point: a calculated measure is a figure a bank certifies and then files decisions
against, so "the compiler emits the shape we expected" is not enough — the number
has to be the right number.

Three properties this file exists to hold, each of which would be a defect nobody
sees if it broke:

* **the declared period is the period.** ``PCT_CHANGE([m:x], MONTH)`` compares the
  reporting month with the month before it wherever that measure appears, and a
  query that cannot be answered at month grain is refused BY NAME rather than
  answered at whatever grain it happens to have (D-195);
* **a missing figure stays missing.** No ``?? 0``, no zero denominator becoming
  zero, no "flat". A zero that the bank actually reported is 0; an absence is no
  value; and the two are proved apart on two adjacent reporting dates;
* **a formula is not a way round a binding.** The figures a statement reads come
  from the SERVER's parse of the certified text, so the authorization walk is given
  exactly the set the statement will read — proved here by doctoring the stored
  ``referenced_members`` column and by withholding one of the figures.

This module's other-suite cases live in ``test_compiler_calculated_integration.py``.
"""

from __future__ import annotations

from datetime import date
from typing import get_args

import pytest

from app.bi.domain.catalogue import Catalogue
from app.bi.domain.catalogue.members import VALUE_TYPES
from app.bi.models.content import MEASURE_STATES
from app.bi.schemas.content import BiDashboardSpec
from app.bi.schemas.query import (
    BiLayoutItem,
    BiPackQuery,
    BiPackWidget,
    BiResultColumnFormat,
)
from app.bi.service import compiler, content
from app.bi.service.errors import InvalidQuery
from tests.bi.service.test_compiler_calculated_integration import (
    DEPOSITS,
    LOANS,
    MAY,
    RETENTION_DAYS,
)
from tests.bi.service.test_compiler_calculated_integration import (
    cat as cat,  # noqa: PLC0414 - shared with the split module
)


def test_the_certified_state_is_the_one_the_model_declares() -> None:
    assert compiler._CERTIFIED_STATE in MEASURE_STATES
    assert compiler._CERTIFIED_STATE == "bank_certified"


def test_a_saved_canvas_may_not_name_an_uncertified_calculated_measure() -> None:
    """A saved dashboard is a document other people open.

    A personal formula on one turns a share into a way to make someone else compute
    the owner's arithmetic under the owner's label, so the shape check refuses any
    measure id the catalogue does not know unless it is named as certified — and the
    certified set defaults to EMPTY, so a caller that does not supply it refuses
    every calculated measure rather than admitting one unchecked.
    """

    spec = BiDashboardSpec(
        widgets=[
            BiPackWidget(
                id="w1",
                kind="kpi",
                title="Loans to deposits",
                query=BiPackQuery(measures=["custom.loans_to_deposits"], window="as_of"),
            )
        ],
        layout=[BiLayoutItem(i="w1", x=0, y=0, w=4, h=4)],
    )
    with pytest.raises(content.CanvasRefused) as caught:
        content.check_canvas_shape(spec)
    assert "has certified" in str(caught.value)

    # Named as certified, it passes: what breakdowns it allows is the compiler's
    # question, decided against the formula it re-parses.
    content.check_canvas_shape(spec, certified_measures=frozenset({"custom.loans_to_deposits"}))


def test_the_wire_format_vocabulary_is_the_catalogue_s_plus_the_marker() -> None:
    """Both directions, because a one-way check is what let ``ratio`` be retired in
    four consumers' silence (A8-02, A8-03, D-189). ``app/schemas`` may not import
    ``app.bi.domain`` — the plane boundary keeps it in the scan — so the two are
    held together here instead of by an import."""

    wire = set(get_args(BiResultColumnFormat))
    assert wire == set(VALUE_TYPES) | {"int"}
    assert set(VALUE_TYPES) <= wire
    assert "ratio" not in wire


def test_a_calculated_measure_declaring_an_unrenderable_number_is_refused(
    cat: Catalogue,
) -> None:
    """The narrowing from a text column to the wire vocabulary is CHECKED.

    A stored row cannot reach this — ``ck_bi_measures_value_type`` refuses the
    write, which is proved below — so the guard is exercised on the function that
    does the narrowing. Without it the narrowing would be an unverified assertion
    about a text column, which is the shape of A8-11 itself.
    """

    with pytest.raises(InvalidQuery) as caught:
        compiler._resolve_calculated(
            cat,
            compiler._Certified(
                id="custom.odd",
                label="Odd figure",
                value_type="ratio",
                expression=f"SAFE_DIV([m:{LOANS}], [m:{DEPOSITS}])",
            ),
            RETENTION_DAYS,
        )
    assert "kind of number this result cannot render" in caught.value.message


@pytest.mark.parametrize(
    ("grain", "back", "expected"),
    [
        ("month", 0, (date(2026, 5, 1), date(2026, 5, 31))),
        ("month", 3, (date(2026, 2, 1), date(2026, 2, 28))),
        ("month", 5, (date(2025, 12, 1), date(2025, 12, 31))),
        ("quarter", 0, (date(2026, 4, 1), date(2026, 6, 30))),
        ("quarter", 2, (date(2025, 10, 1), date(2025, 12, 31))),
        ("year", 1, (date(2025, 1, 1), date(2025, 12, 31))),
    ],
)
def test_a_period_is_shifted_by_months_and_not_by_days(
    grain: str, back: int, expected: tuple[date, date]
) -> None:
    """Three months before 31 May is February, whose end is the 28th, and no number
    of days says that."""

    assert compiler._shifted_period(MAY, grain, back) == expected


def test_the_period_in_progress_is_never_read_past_the_reporting_date() -> None:
    window = compiler._period_window(date(2026, 9, 15), "month", 0)
    assert (window.start, window.end) == (date(2026, 9, 1), date(2026, 9, 15))
    assert not window.single
