"""FC-1 rate helpers: implied-quote consistency and the reprice-to-input acid test.

Each helper must (a) return the quote implied by a curve and (b) reprice that
same quote to a zero residual; on a solved curve every input instrument reprices
to within 1e-8 — the invariant the whole platform pins.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.domain.curves.calendars import USA, next_imm_date
from app.domain.curves.conventions import DayCount, year_fraction
from app.domain.curves.instruments import (
    DEFAULT_FUTURES_VOL,
    DepositHelper,
    FraHelper,
    FuturesHelper,
    InstrumentSet,
    OisHelper,
    SwapHelper,
    convexity_adjustment,
    usd_depo_irs_vs_6m,
    usd_sofr_ois,
    usd_swap_vs_3m,
)
from app.domain.curves.multicurve import (
    MarketQuote,
    SolvedCurve,
    build_curve_set,
)

AS_OF = date(2023, 12, 29)


def _flat_curve(zero: float = 0.05) -> SolvedCurve:
    """A curve with a constant continuously-compounded zero rate."""
    nodes = (
        date(2024, 6, 29),
        date(2024, 12, 30),
        date(2025, 12, 29),
        date(2028, 12, 29),
        date(2033, 12, 29),
        date(2053, 12, 29),
    )
    return SolvedCurve(AS_OF, nodes, tuple(zero for _ in nodes))


def test_deposit_implied_quote_and_residual() -> None:
    curve = _flat_curve(0.05)
    depo = DepositHelper("3M", USA)
    implied = depo.implied_quote(curve, curve)
    # ~cont 5% rendered as an Act/360 simple rate: 0.05 * 360/365 plus small convexity.
    assert implied == pytest.approx(0.0496, abs=5e-4)
    assert depo.reprice_residual(implied, curve, curve) == pytest.approx(0.0, abs=1e-12)
    assert abs(depo.reprice_residual(implied + 0.001, curve, curve)) > 1e-6


def test_fra_implied_quote_and_residual() -> None:
    curve = _flat_curve(0.05)
    fra = FraHelper("3M", "6M", USA)
    implied = fra.implied_quote(curve, curve)
    assert fra.reprice_residual(implied, curve, curve) == pytest.approx(0.0, abs=1e-12)
    assert fra.reprice_residual(implied - 0.002, curve, curve) == pytest.approx(0.002, abs=1e-9)


def test_swap_par_reprices_on_curve() -> None:
    curve = _flat_curve(0.04)
    swap = SwapHelper("5Y", USA)
    par = swap.implied_quote(curve, curve)
    assert par == pytest.approx(0.04, abs=2e-3)  # near the flat zero level
    assert swap.reprice_residual(par, curve, curve) == pytest.approx(0.0, abs=1e-12)


def test_ois_par_reprices_on_curve() -> None:
    curve = _flat_curve(0.045)
    ois = OisHelper("2Y", USA)
    par = ois.implied_quote(curve, curve)
    assert ois.reprice_residual(par, curve, curve) == pytest.approx(0.0, abs=1e-12)
    # OIS ignores the projection curve (self-discounting); passing a different one is inert.
    other = _flat_curve(0.09)
    assert ois.implied_quote(curve, other) == pytest.approx(par)


@pytest.mark.parametrize("interpolation", ["log_linear_df", "monotone_convex", "pchip"])
def test_acid_single_curve_reprices_to_input(interpolation: str) -> None:
    """Build a self-discounting curve from synthetic par quotes; every helper reprices."""
    deposits = [
        MarketQuote(DepositHelper(t, USA), r)
        for t, r in [("1M", 0.0535), ("3M", 0.0530), ("6M", 0.0515)]
    ]
    swaps = [
        MarketQuote(SwapHelper(t, USA), r)
        for t, r in [
            ("1Y", 0.0480),
            ("2Y", 0.0430),
            ("3Y", 0.0405),
            ("5Y", 0.0390),
            ("7Y", 0.0388),
            ("10Y", 0.0385),
            ("30Y", 0.0370),
        ]
    ]
    quotes = [*deposits, *swaps]
    curve_set = build_curve_set(
        as_of=AS_OF, calendar=USA, discount_quotes=quotes, interpolation=interpolation
    )
    worst = max(
        abs(q.helper.reprice_residual(q.quote, curve_set.discount, curve_set.projection))
        for q in quotes
    )
    assert worst < 1e-8


def test_instrument_set_pillar_dates_match_conventions() -> None:
    family = usd_depo_irs_vs_6m(USA)
    assert isinstance(family, InstrumentSet)
    assert family.ric_chain == "0#USDZ=R"
    # Deposits roll from spot (2024-01-03), swaps to their tenor.
    dates = family.pillar_dates(AS_OF, USA)
    assert dates[0] == USA.add_tenor(USA.spot_date(AS_OF), "1W")
    assert dates[-1] == USA.add_tenor(USA.spot_date(AS_OF), "30Y")
    assert list(dates) == sorted(dates)


def test_projection_instrument_set_flag() -> None:
    assert usd_swap_vs_3m(USA).on_projection is True
    assert usd_sofr_ois(USA).on_projection is False


# --------------------------------------------------------------------------- #
# FC-G3 forward-start helpers                                                  #
# --------------------------------------------------------------------------- #

IMM = next_imm_date(AS_OF)  # 2024-03-20


def test_forward_start_deposit_dates_and_reprice() -> None:
    curve = _flat_curve(0.05)
    fwd = DepositHelper("3M", USA, forward_start=IMM)
    # The pillar (maturity) date rolls from the forward start, not the spot date.
    assert fwd.pillar_date(USA, AS_OF) == USA.add_tenor(IMM, "3M")
    assert fwd.pillar_date(USA, AS_OF) != DepositHelper("3M", USA).pillar_date(USA, AS_OF)
    implied = fwd.implied_quote(curve, curve)
    assert fwd.reprice_residual(implied, curve, curve) == pytest.approx(0.0, abs=1e-12)
    # On a curve that slopes across the two accrual windows, the forward-start deposit
    # (Mar-Jun) and the spot deposit (Jan-Apr) imply materially different quotes. Early
    # nodes are required so the forward is not flat-extrapolated before the first pillar.
    sloped = SolvedCurve(
        AS_OF,
        (date(2024, 2, 1), date(2024, 5, 1), date(2024, 8, 1), date(2025, 6, 20)),
        (0.056, 0.050, 0.046, 0.042),
    )
    assert abs(
        fwd.implied_quote(sloped, sloped)
        - DepositHelper("3M", USA).implied_quote(sloped, sloped)
    ) > 1e-3


def test_forward_start_swap_and_ois_reprice_at_par() -> None:
    curve = _flat_curve(0.04)
    fwd_swap = SwapHelper("3Y", USA, forward_start=date(2025, 3, 20))
    assert fwd_swap.pillar_date(USA, AS_OF) == USA.add_tenor(date(2025, 3, 20), "3Y")
    par = fwd_swap.implied_quote(curve, curve)
    assert fwd_swap.reprice_residual(par, curve, curve) == pytest.approx(0.0, abs=1e-12)

    fwd_ois = OisHelper("2Y", USA, forward_start=date(2025, 3, 20))
    par_ois = fwd_ois.implied_quote(curve, curve)
    assert fwd_ois.reprice_residual(par_ois, curve, curve) == pytest.approx(0.0, abs=1e-12)


def test_forward_start_bootstrap_reprices() -> None:
    """A forward-start deposit strip bootstraps and every helper reprices to input."""
    quotes = [
        MarketQuote(DepositHelper(t, USA, forward_start=IMM), r)
        for t, r in [("1M", 0.0532), ("3M", 0.0528), ("6M", 0.0515), ("1Y", 0.0495)]
    ]
    quotes.sort(key=lambda q: q.helper.pillar_date(USA, AS_OF))
    curve_set = build_curve_set(as_of=AS_OF, calendar=USA, discount_quotes=quotes)
    worst = max(
        abs(q.helper.reprice_residual(q.quote, curve_set.discount, curve_set.projection))
        for q in quotes
    )
    assert worst < 1e-8


# --------------------------------------------------------------------------- #
# FC-6a futures convexity                                                      #
# --------------------------------------------------------------------------- #


def test_convexity_adjustment_formula_and_guards() -> None:
    # fwd = futures - 0.5 * sigma^2 * t1 * t2 ; the returned value is that subtrahend.
    assert convexity_adjustment(0.25, 0.5, 0.01) == pytest.approx(0.5 * 1e-4 * 0.25 * 0.5)
    # Grows with maturity and is non-negative; zero vol => zero adjustment.
    assert convexity_adjustment(5.0, 5.25, 0.01) > convexity_adjustment(0.25, 0.5, 0.01)
    assert convexity_adjustment(1.0, 1.25, 0.0) == 0.0
    with pytest.raises(Exception):  # noqa: B017 - InstrumentSetError on bad times
        convexity_adjustment(0.5, 0.25, 0.01)


def test_futures_helper_reprices_and_exceeds_fra_by_convexity() -> None:
    curve = _flat_curve(0.05)
    start, end = IMM, USA.add_tenor(IMM, "3M")
    futures = FuturesHelper(start, end, USA, sigma=DEFAULT_FUTURES_VOL)
    # The futures quote implied by the curve reprices to zero residual.
    implied = futures.implied_quote(curve, curve)
    assert futures.reprice_residual(implied, curve, curve) == pytest.approx(0.0, abs=1e-12)

    # The convexity adjustment lifts the futures quote above the equivalent FRA forward.
    fra_forward = (curve.discount(start) / curve.discount(end) - 1.0) / year_fraction(
        start, end, DayCount.ACT_360
    )
    t1 = year_fraction(AS_OF, start, DayCount.ACT_365F)
    t2 = year_fraction(AS_OF, end, DayCount.ACT_365F)
    adj = convexity_adjustment(t1, t2, DEFAULT_FUTURES_VOL)
    assert implied == pytest.approx(fra_forward + adj)
    assert futures.forward_from_futures(implied, AS_OF) == pytest.approx(fra_forward)
    # With zero vol a futures helper collapses onto the plain forward.
    flat = FuturesHelper(start, end, USA, sigma=0.0)
    assert flat.implied_quote(curve, curve) == pytest.approx(fra_forward)


def test_futures_convexity_grows_with_maturity() -> None:
    """A 5Y-forward futures carries a materially larger convexity than a 3M-forward one.

    The convexity is the gap between the curve-implied futures quote and the
    equivalent forward (``forward_from_futures``); over identical 3M windows it
    widens sharply with the forward start.
    """
    curve = _flat_curve(0.045)
    near = FuturesHelper(IMM, USA.add_tenor(IMM, "3M"), USA)
    far_start = USA.add_tenor(IMM, "5Y")
    far = FuturesHelper(far_start, USA.add_tenor(far_start, "3M"), USA)

    def convexity_gap(futures: FuturesHelper) -> float:
        quote = futures.implied_quote(curve, curve)
        return quote - futures.forward_from_futures(quote, AS_OF)

    near_gap, far_gap = convexity_gap(near), convexity_gap(far)
    assert 0.0 < near_gap < far_gap
    assert far_gap > 10 * near_gap
