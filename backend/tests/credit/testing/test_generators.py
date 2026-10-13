"""Credit generator validity and public-engine metamorphic behavior."""

from dataclasses import replace
from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from app.domain.capital.loan_classification import classify_book
from app.domain.credit.concentration_monitor import ConcentrationLimit, monitor_concentration
from tests.credit.testing.assertions import (
    TOLERANCE,
    assert_concentration_reconciles,
    assert_credit_invariants,
    assert_credit_ratio_unchanged,
)
from tests.credit.testing.generators import (
    CreditBook,
    LargeExposureBook,
    credit_books,
    credit_grid,
    large_exposure_books,
)
from tests.support.assertions import assert_unchanged


@given(book=credit_books(), scale=st.integers(1, 10))
def test_credit_books_are_consistent_and_ratios_scale(book: CreditBook, scale: int) -> None:
    grid = credit_grid()
    result = classify_book(book.loans, grid)
    assert_credit_invariants(result)
    assert tuple(loan.grade for loan in result.loans) == book.grades
    assert tuple(loan.provision_required_ghs for loan in result.loans) == book.booked_provisions
    for input_loan, classified in zip(book.loans, result.loans, strict=True):
        assert (
            input_loan.exposure_ghs >= 0
            and input_loan.exposure_ghs == input_loan.exposure_ghs.quantize(Decimal("0.0001"))
        )
        assert classified.non_performing == (input_loan.ifrs9_stage == 3)
        assert (input_loan.ifrs9_stage == 1) == (classified.grade == "standard")
    scaled = tuple(replace(loan, exposure_ghs=loan.exposure_ghs * scale) for loan in book.loans)
    assert_credit_ratio_unchanged(classify_book(scaled, grid), result)
    reordered = classify_book(tuple(reversed(book.loans)), grid)
    assert_unchanged(
        replace(reordered, loans=tuple(reversed(reordered.loans))),
        result,
        because="Credit: reordering changed grade totals or per-loan classification",
    )


@given(book=large_exposure_books(), scale=st.integers(1, 10))
def test_large_exposures_are_grouped_and_near_the_limit(
    book: LargeExposureBook, scale: int
) -> None:
    limit = ConcentrationLimit("single_name", "share_of_capital_pct", book.limit_pct)
    before = monitor_concentration(book.exposures, capital_base_ghs=book.capital, limits=(limit,))
    assert_concentration_reconciles(before)
    dimension = before.dimension("single_name")
    assert dimension is not None
    group = next(b for b in dimension.buckets if b.key == "synthetic-group")
    assert group.loan_count == 2
    assert group.share_of_capital_pct is not None
    TOLERANCE.percentages.assert_close(
        group.share_of_capital_pct,
        book.target_pct,
        because="Credit: generated connected-group limit boundary",
    )
    scaled = monitor_concentration(
        tuple(replace(e, ead=e.ead * scale) for e in book.exposures),
        capital_base_ghs=book.capital * scale,
        limits=(limit,),
    )
    scaled_dimension = scaled.dimension("single_name")
    assert scaled_dimension is not None
    assert tuple(b.share_of_capital_pct for b in scaled_dimension.buckets) == tuple(
        b.share_of_capital_pct for b in dimension.buckets
    )
    assert_unchanged(
        monitor_concentration(
            tuple(reversed(book.exposures)), capital_base_ghs=book.capital, limits=(limit,)
        ),
        before,
        because="Credit: reordering changed connected-group concentration",
    )
