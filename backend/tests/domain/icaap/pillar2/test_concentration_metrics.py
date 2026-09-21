"""HHI, Gini and CRn: the reference table, and the invariants D-017 corrected.

The reference cases are hand-computable and are reproduced from the regulatory
audit §5.3. The properties are the ones that would have caught the spec's
original claim that a uniform book scores zero on every measure: it does on
Gini, and it must not on HHI, whose floor is 1/N.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.domain.icaap.pillar2 import concentration_metrics as cm
from app.domain.icaap.units import ratio

SETTINGS = settings(max_examples=300, deadline=None)
_BOOKS = st.lists(st.integers(min_value=1, max_value=10**6), min_size=1, max_size=30)


def book(*values: int | str) -> list[Decimal]:
    return [Decimal(value) for value in values]


REFERENCE = [
    ("A", book(10, 10, 10, 10), "0.25", "0", "0", "0", "0.25", "0.50", "0.75", "1"),
    ("B", book(1, 2, 3, 4), "0.30", "0.066667", "0.25", "0.333333", "0.40", "0.70", "0.90", "1"),
    ("C", book(97, 1, 1, 1), "0.9412", "0.9216", "0.72", "0.96", "0.97", "0.98", "0.99", "1"),
    ("D", book(5), "1", None, None, None, "1", "1", "1", "1"),
    ("E", book(50, 50), "0.50", "0", "0", "0", "0.50", "1", "1", "1"),
    ("F", book(5, 3, 2), "0.38", "0.07", "0.20", "0.30", "0.50", "0.80", "1", "1"),
]


@pytest.mark.parametrize(
    ("case", "values", "hhi", "hhi_star", "gini", "gini_star", "cr1", "cr2", "cr3", "cr5"),
    REFERENCE,
    ids=[row[0] for row in REFERENCE],
)
def test_the_reference_table(  # noqa: PLR0913 - one column per metric
    case: str,
    values: list[Decimal],
    hhi: str,
    hhi_star: str | None,
    gini: str | None,
    gini_star: str | None,
    cr1: str,
    cr2: str,
    cr3: str,
    cr5: str,
) -> None:
    assert cm.hhi(values) == Decimal(hhi), case
    assert cm.hhi_normalised(values) == (None if hhi_star is None else Decimal(hhi_star))
    assert cm.gini(values) == (None if gini is None else Decimal(gini))
    assert cm.gini_normalised(values) == (None if gini_star is None else Decimal(gini_star))
    assert cm.concentration_ratio(values, 1) == Decimal(cr1)
    assert cm.concentration_ratio(values, 2) == Decimal(cr2)
    assert cm.concentration_ratio(values, 3) == Decimal(cr3)
    assert cm.concentration_ratio(values, 5) == Decimal(cr5)


def test_a_single_exposure_has_no_measurable_inequality() -> None:
    """Reporting 0 for one loan would read as a perfectly diversified book."""
    assert cm.gini(book(5)) is None
    assert cm.gini_normalised(book(5)) is None
    assert cm.hhi(book(5)) == Decimal(1)


def test_concentration_on_a_declared_capital_base() -> None:
    assert cm.concentration_ratio_on_capital(book(1, 2, 3, 4), 1, Decimal(8)) == Decimal("0.5")
    assert cm.concentration_ratio_on_capital(book(1), 1, Decimal(0)) is None
    assert cm.concentration_ratio_on_capital(book(1), 1, None) is None


def test_non_positive_exposures_are_excluded_before_n_is_counted() -> None:
    assert cm.hhi(book(10, 10, 0, -5)) == cm.hhi(book(10, 10))
    assert cm.gini(book(10, 0)) is None
    assert cm.hhi([]) is None


def test_an_empty_top_n_is_refused() -> None:
    with pytest.raises(ValueError, match="concentration_ratio_n_not_positive"):
        cm.concentration_ratio(book(1, 2), 0)


@SETTINGS
@given(count=st.integers(min_value=2, max_value=40), size=st.integers(min_value=1, max_value=10**6))
def test_a_uniform_book_is_zero_on_gini_and_one_over_n_on_hhi(count: int, size: int) -> None:
    values = [Decimal(size)] * count
    assert cm.gini(values) == Decimal(0)
    assert cm.hhi(values) == ratio(Decimal(1) / Decimal(count))
    assert cm.hhi_normalised(values) == Decimal(0)


@SETTINGS
@given(values=_BOOKS, n=st.integers(min_value=1, max_value=30))
def test_the_metrics_stay_inside_their_bounds(values: list[int], n: int) -> None:
    exposures = book(*values)
    count = len(exposures)
    measured = cm.hhi(exposures)
    assert measured is not None
    assert ratio(Decimal(1) / Decimal(count)) <= measured <= Decimal(1)
    inequality = cm.gini(exposures)
    if inequality is not None:
        assert Decimal(0) <= inequality <= ratio(Decimal(count - 1) / Decimal(count))
    top = cm.concentration_ratio(exposures, n)
    assert top is not None
    assert top >= ratio(Decimal(min(n, count)) / Decimal(count))


@SETTINGS
@given(values=_BOOKS, shift=st.integers(min_value=0, max_value=29), factor=st.integers(1, 1000))
def test_order_and_scale_do_not_change_a_metric(values: list[int], shift: int, factor: int) -> None:
    exposures = book(*values)
    rotated = exposures[shift:] + exposures[:shift]
    scaled = [value * Decimal(factor) for value in exposures]
    for permuted in (rotated, scaled):
        assert cm.hhi(permuted) == cm.hhi(exposures)
        assert cm.gini(permuted) == cm.gini(exposures)
        assert cm.concentration_ratio(permuted, 3) == cm.concentration_ratio(exposures, 3)


@SETTINGS
@given(values=_BOOKS, delta=st.integers(min_value=1, max_value=1000), n=st.integers(1, 10))
def test_a_transfer_to_a_larger_name_never_lowers_concentration(
    values: list[int], delta: int, n: int
) -> None:
    """The reverse Pigou–Dalton transfer: concentration is monotone under it."""
    exposures = sorted(book(*values))
    if len(exposures) < 2 or exposures[0] <= Decimal(delta):
        return
    before_hhi = cm.hhi(exposures)
    before_gini = cm.gini(exposures)
    before_cr = cm.concentration_ratio(exposures, n)
    after = list(exposures)
    after[0] -= Decimal(delta)
    after[-1] += Decimal(delta)
    after_hhi = cm.hhi(after)
    assert before_hhi is not None and after_hhi is not None
    assert after_hhi >= before_hhi
    if before_gini is not None:
        measured = cm.gini(after)
        assert measured is not None and measured >= before_gini
    assert before_cr is not None
    measured_cr = cm.concentration_ratio(after, n)
    assert measured_cr is not None and measured_cr >= before_cr
