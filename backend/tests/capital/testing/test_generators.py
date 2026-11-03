"""Synthetic book validity and capital metamorphic checks."""

from dataclasses import replace
from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from app.capital.domain.engine import MONEY, CapitalFact, compute_capital_ratios, compute_rwa
from tests.capital.testing.assertions import (
    TOLERANCE,
    assert_capital_invariants,
    assert_capital_ratios_unchanged,
    assert_zero_exposure_unchanged,
)
from tests.capital.testing.generators import CapitalBook, capital_books, capital_params


@given(book=capital_books(), scale=st.integers(1, 10))
def test_books_are_balanced_and_scaling_preserves_ratios(book: CapitalBook, scale: int) -> None:
    facts, params = book.facts, capital_params()
    totals = {
        side: sum(
            (f.amount for f in facts if f.fact_group == "balance_sheet" and f.side == side),
            Decimal(0),
        )
        for side in ("asset", "liability", "equity")
    }
    assert totals["asset"] == totals["liability"] + totals["equity"]
    assert all(
        f.amount.is_finite() and f.amount >= 0 and f.amount == f.amount.quantize(MONEY)
        for f in facts
    )
    rwa = compute_rwa(facts, params)
    before = compute_capital_ratios(facts, rwa, params)
    assert_capital_invariants(rwa, before)
    TOLERANCE.assert_close(
        before.car_pct, book.target_car_pct, because="Capital: generated CAR target"
    )
    scaled = tuple(replace(f, amount=f.amount * scale) for f in facts)
    after = compute_capital_ratios(scaled, compute_rwa(scaled, params), params)
    assert_capital_ratios_unchanged(after, before)
    reordered = tuple(reversed(facts))
    assert compute_rwa(reordered, params) == rwa
    assert compute_capital_ratios(reordered, rwa, params) == before
    zero = (
        *facts,
        CapitalFact("loan_exposure", "synthetic_zero", Decimal(0), risk_weight_code="RW100"),
    )
    assert_zero_exposure_unchanged(
        compute_capital_ratios(zero, compute_rwa(zero, params), params), before
    )
