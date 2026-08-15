"""FC-6b FX-forward curve: covered-interest-parity identity + acid tests.

Pins the CIP relation ``F(t) = S * DF_base(t) / DF_quote(t)`` on the pure domain
module. The four properties the spec's construction acid tests demand:

1. where ``DF_base == DF_quote`` the forward equals spot (identity);
2. forward points grow monotonically with the (quote - base) rate differential;
3. CIP round-trips — the forward reproduces the discount-factor ratio exactly;
4. the value-based ``input_digest`` is reproducible and input-sensitive.

Plus the optional cross-currency ``basis_bps`` seam (zero by default) and the
domain error contract.
"""

from __future__ import annotations

import math
from datetime import date

import pytest

from app.domain.curves.conventions import DayCount, year_fraction
from app.domain.curves.fx_forward import (
    FxForwardError,
    build_fx_forward_curve,
)
from app.domain.curves.multicurve import SolvedCurve

AS_OF = date(2026, 8, 7)
# A grid of dates roughly 3M / 6M / 1Y / 2Y / 3Y out.
GRID = (
    date(2026, 11, 9),
    date(2027, 2, 8),
    date(2027, 8, 9),
    date(2028, 8, 7),
    date(2029, 8, 7),
)


def _flat_curve(zero: float) -> SolvedCurve:
    """A flat continuously-compounded (Act/365F) discount curve off ``AS_OF``."""
    nodes = (date(2026, 11, 9), date(2027, 8, 9), date(2029, 8, 7), date(2036, 8, 7))
    return SolvedCurve(AS_OF, nodes, tuple(zero for _ in nodes))


# ---------------------------------------------------------------------------
# 1. Identity: where DF_base == DF_quote the forward is spot
# ---------------------------------------------------------------------------


def test_identical_curves_reproduce_spot() -> None:
    """Two identical discount curves => forward == spot at every date, zero points."""
    spot = 12.5
    curve = _flat_curve(0.06)
    result = build_fx_forward_curve(spot, curve, curve, GRID, base_ccy="USD", quote_ccy="GHS")
    assert len(result.points) == len(GRID)
    for point in result.points:
        assert point.forward_rate == pytest.approx(spot, rel=0, abs=1e-12)
        assert point.forward_points == pytest.approx(0.0, abs=1e-12)
        # DF_base == DF_quote at every date by construction.
        assert point.df_base == pytest.approx(point.df_quote, abs=1e-15)


def test_spot_node_at_valuation_date_is_spot() -> None:
    """A date equal to the valuation date is the spot node (tau=0, DF=1, points=0)."""
    spot = 12.5
    result = build_fx_forward_curve(
        spot, _flat_curve(0.20), _flat_curve(0.05), (AS_OF, *GRID)
    )
    spot_node = result.points[0]
    assert spot_node.date == AS_OF
    assert spot_node.year_fraction == 0.0
    assert spot_node.df_base == 1.0
    assert spot_node.df_quote == 1.0
    assert spot_node.forward_rate == pytest.approx(spot, abs=1e-12)
    assert spot_node.forward_points == pytest.approx(0.0, abs=1e-12)


# ---------------------------------------------------------------------------
# 2. Forward points grow monotonically with the (quote - base) differential
# ---------------------------------------------------------------------------


def test_forward_points_grow_with_rate_differential() -> None:
    """Higher quote rate (base fixed) => larger forward points at every tenor.

    CIP: with ``r_quote > r_base`` the base currency is at a forward premium in
    quote terms (``F > S``); widening the differential widens the premium.
    """
    spot = 12.5
    base = _flat_curve(0.05)  # base leg pinned
    quote_rates = [0.05, 0.10, 0.20, 0.30]
    # Forward points at the longest tenor for each quote rate.
    longest = GRID[-1]
    series = [
        build_fx_forward_curve(spot, base, _flat_curve(rq), (longest,)).points[0].forward_points
        for rq in quote_rates
    ]
    # r_quote == r_base gives zero points; strictly increasing thereafter.
    assert series[0] == pytest.approx(0.0, abs=1e-12)
    assert all(later > earlier for earlier, later in zip(series, series[1:], strict=False))


def test_higher_yielding_currency_trades_at_forward_discount() -> None:
    """If the base out-yields the quote, the base is at a forward discount (F < S)."""
    spot = 12.5
    result = build_fx_forward_curve(spot, _flat_curve(0.25), _flat_curve(0.05), GRID)
    # r_base > r_quote => DF_base < DF_quote => F < S at every future date.
    assert all(point.forward_rate < spot for point in result.points)
    assert all(point.forward_points < 0.0 for point in result.points)
    # Monotone: the discount deepens with tenor.
    points = [p.forward_points for p in result.points]
    assert all(later < earlier for earlier, later in zip(points, points[1:], strict=False))


# ---------------------------------------------------------------------------
# 3. CIP round-trips
# ---------------------------------------------------------------------------


def test_cip_round_trips_from_discount_factors() -> None:
    """forward / spot == DF_base / DF_quote at every node (textbook CIP)."""
    spot = 12.5
    result = build_fx_forward_curve(spot, _flat_curve(0.22), _flat_curve(0.045), GRID)
    for point in result.points:
        ratio = point.df_base / point.df_quote
        assert point.forward_rate / spot == pytest.approx(ratio, rel=1e-12)
        assert point.forward_points == pytest.approx(point.forward_rate - spot, abs=1e-12)


def test_forward_matches_closed_form_flat_curves() -> None:
    """Against the closed form F = S * exp(-(r_base - r_quote) * tau) for flat curves."""
    spot, r_base, r_quote = 12.5, 0.22, 0.045
    result = build_fx_forward_curve(spot, _flat_curve(r_base), _flat_curve(r_quote), GRID)
    for point in result.points:
        tau = year_fraction(AS_OF, point.date, DayCount.ACT_365F)
        expected = spot * math.exp(-(r_base - r_quote) * tau)
        assert point.forward_rate == pytest.approx(expected, rel=1e-10)


# ---------------------------------------------------------------------------
# 4. Cross-currency basis seam (default zero, additive on the base leg)
# ---------------------------------------------------------------------------


def test_basis_is_zero_by_default_and_shifts_the_base_leg() -> None:
    """basis_bps defaults to 0 (textbook CIP); a positive basis moves the forward."""
    spot = 12.5
    base, quote = _flat_curve(0.22), _flat_curve(0.045)
    textbook = build_fx_forward_curve(spot, base, quote, GRID)
    with_basis = build_fx_forward_curve(spot, base, quote, GRID, basis_bps=50.0)
    assert textbook.basis_bps == 0.0
    for zero, shifted in zip(textbook.points, with_basis.points, strict=False):
        # basis 0 leaves the base leg untouched.
        assert zero.df_base_adjusted == pytest.approx(zero.df_base, abs=1e-15)
        # A positive basis shrinks DF_base_adjusted => lowers the forward (F ~ DF_base).
        assert shifted.df_base_adjusted < shifted.df_base
        assert shifted.forward_rate < zero.forward_rate
        # Closed form: the multiplicative shift is exp(-basis * tau) on the base leg.
        tau = year_fraction(AS_OF, zero.date, DayCount.ACT_365F)
        assert shifted.forward_rate / zero.forward_rate == pytest.approx(
            math.exp(-(50.0 / 1e4) * tau), rel=1e-12
        )


# ---------------------------------------------------------------------------
# Digest: reproducible + input-sensitive
# ---------------------------------------------------------------------------


def test_digest_is_reproducible_and_input_sensitive() -> None:
    spot = 12.5
    base, quote = _flat_curve(0.22), _flat_curve(0.045)
    a = build_fx_forward_curve(spot, base, quote, GRID, base_ccy="USD", quote_ccy="GHS")
    b = build_fx_forward_curve(spot, base, quote, GRID, base_ccy="USD", quote_ccy="GHS")
    assert a.input_digest == b.input_digest
    assert len(a.input_digest) == 64
    # A different spot changes the digest.
    c = build_fx_forward_curve(13.0, base, quote, GRID, base_ccy="USD", quote_ccy="GHS")
    assert c.input_digest != a.input_digest
    # A different basis changes the digest.
    d = build_fx_forward_curve(
        spot, base, quote, GRID, basis_bps=25.0, base_ccy="USD", quote_ccy="GHS"
    )
    assert d.input_digest != a.input_digest


# ---------------------------------------------------------------------------
# Error contract
# ---------------------------------------------------------------------------


def test_rejects_bad_spot() -> None:
    curve = _flat_curve(0.05)
    with pytest.raises(FxForwardError):
        build_fx_forward_curve(0.0, curve, curve, GRID)
    with pytest.raises(FxForwardError):
        build_fx_forward_curve(-1.0, curve, curve, GRID)
    with pytest.raises(FxForwardError):
        build_fx_forward_curve(float("nan"), curve, curve, GRID)


def test_rejects_mismatched_valuation_dates() -> None:
    base = _flat_curve(0.05)
    quote = SolvedCurve(
        date(2026, 8, 6), (date(2027, 8, 9), date(2029, 8, 7)), (0.05, 0.05)
    )
    with pytest.raises(FxForwardError):
        build_fx_forward_curve(12.5, base, quote, GRID)


def test_rejects_non_increasing_and_pre_spot_dates() -> None:
    curve = _flat_curve(0.05)
    with pytest.raises(FxForwardError):
        build_fx_forward_curve(12.5, curve, curve, (GRID[1], GRID[0]))
    with pytest.raises(FxForwardError):
        build_fx_forward_curve(12.5, curve, curve, (date(2026, 8, 6),))
    with pytest.raises(FxForwardError):
        build_fx_forward_curve(12.5, curve, curve, ())
