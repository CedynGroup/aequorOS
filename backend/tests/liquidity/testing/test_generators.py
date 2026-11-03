"""Public engines establish generator validity and monetary relations."""

from dataclasses import replace
from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from app.liquidity.domain.engine import MONEY, compute_currency_gaps, compute_lcr, compute_nsfr
from tests.liquidity.testing.assertions import (
    TOLERANCE,
    assert_ladder_totals,
    assert_liquidity_invariants,
    assert_liquidity_ratios_unchanged,
)
from tests.liquidity.testing.generators import (
    LiquidityBook,
    liquidity_books,
    liquidity_ladders,
    liquidity_params,
)


@given(book=liquidity_books(), scale=st.integers(1, 10))
def test_books_are_computable_near_targets_and_scaling_preserves_ratios(
    book: LiquidityBook, scale: int
) -> None:
    facts, params = book.facts, liquidity_params()
    assert all(
        f.amount.is_finite() and f.amount >= 0 and f.amount == f.amount.quantize(MONEY)
        for f in facts
    )
    totals = {
        side: sum(
            (f.amount for f in facts if f.fact_group == "balance_sheet" and f.side == side),
            Decimal(0),
        )
        for side in ("asset", "liability", "equity")
    }
    assert totals["asset"] == totals["liability"] + totals["equity"]
    loans = sum((f.amount for f in facts if f.fact_group == "loan_exposure"), Decimal(0))
    assert loans == next(f.amount for f in facts if f.category == "loans_gross")
    before = compute_lcr(facts, params), compute_nsfr(facts, params)
    assert_liquidity_invariants(*before)
    TOLERANCE.assert_close(
        before[0].lcr_pct, book.target_lcr_pct, because="Liquidity: generated LCR target"
    )
    TOLERANCE.assert_close(
        before[1].nsfr_pct, book.target_nsfr_pct, because="Liquidity: generated NSFR target"
    )
    scaled = tuple(replace(f, amount=f.amount * scale) for f in facts)
    assert_liquidity_ratios_unchanged(
        (compute_lcr(scaled, params), compute_nsfr(scaled, params)), before
    )
    assert (
        compute_lcr(tuple(reversed(facts)), params),
        compute_nsfr(tuple(reversed(facts)), params),
    ) == before


@given(ladders=liquidity_ladders())
def test_ladder_buckets_reconcile_and_currency_order_preserves_totals(
    ladders: dict[str, dict[str, list[Decimal]]],
) -> None:
    result = compute_currency_gaps(ladders, "GHS")
    assert_ladder_totals(result)
    assert compute_currency_gaps(dict(reversed(tuple(ladders.items()))), "GHS") == result
