"""FX generator validity, scaling, ordering and currency round trips."""

from dataclasses import replace

from hypothesis import given
from hypothesis import strategies as st

from app.fx.domain.engine import MONEY, compute_nop, money
from app.live.domain.positions.fx import fx_position_refusal
from tests.fx.testing.assertions import (
    TOLERANCE,
    assert_currency_round_trip,
    assert_fx_ratios_unchanged,
    assert_nop_invariants,
)
from tests.fx.testing.generators import FxBook, fx_books
from tests.support.assertions import assert_unchanged


@given(book=fx_books(), scale=st.integers(1, 10))
def test_fx_books_are_valued_and_metamorphic(book: FxBook, scale: int) -> None:
    for p in book.positions:
        assert p.assets_ccy >= 0 and p.liabilities_ccy >= 0 and p.spot_ghs > 0
        assert p.net_ccy == p.assets_ccy - p.liabilities_ccy + p.net_derivatives_ccy
        assert all(
            v.is_finite() and v == v.quantize(MONEY)
            for v in (p.net_ccy, p.net_ghs, p.assets_ccy, p.liabilities_ccy, p.net_derivatives_ccy)
        )
        assert fx_position_refusal(p.currency, p.net_ccy, p.net_ghs, p.spot_ghs) is None
        assert_currency_round_trip(money(p.net_ghs / p.spot_ghs), p.net_ccy, currency=p.currency)
    before = compute_nop(
        book.positions, book.tier1, book.single_limit_pct, book.aggregate_limit_pct
    )
    assert_nop_invariants(before)
    TOLERANCE.ratios.assert_close(
        before.nop_pct_tier1, book.target_pct, because="FX: generated aggregate boundary"
    )
    TOLERANCE.ratios.assert_close(
        before.single_ccy_max_pct,
        book.target_pct / 2,
        because="FX: generated single-currency boundary",
    )
    scaled = tuple(
        replace(
            p,
            net_ghs=p.net_ghs * scale,
            net_ccy=p.net_ccy * scale,
            assets_ccy=p.assets_ccy * scale,
            liabilities_ccy=p.liabilities_ccy * scale,
            net_derivatives_ccy=p.net_derivatives_ccy * scale,
        )
        for p in book.positions
    )
    assert_fx_ratios_unchanged(
        compute_nop(scaled, book.tier1 * scale, book.single_limit_pct, book.aggregate_limit_pct),
        before,
    )
    assert_unchanged(
        compute_nop(
            tuple(reversed(book.positions)),
            book.tier1,
            book.single_limit_pct,
            book.aggregate_limit_pct,
        ),
        before,
        because="FX: reordering changed NOP or disclosure lines",
    )
