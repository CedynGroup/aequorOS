"""FC-2 multi-curve solve, forward-grid output, output-basis conversion and digest."""

from __future__ import annotations

from datetime import date

import pytest

from app.domain.curves.calendars import USA, BusinessDayConvention
from app.domain.curves.conventions import DayCount
from app.domain.curves.instruments import DepositHelper, OisHelper, SwapHelper
from app.domain.curves.multicurve import (
    MarketQuote,
    MultiCurveError,
    SolvedCurve,
    build_curve_set,
    convert_basis,
    forward_grid,
    forward_grid_for_frequency,
)

AS_OF = date(2023, 12, 29)


def _flat_curve(zero: float) -> SolvedCurve:
    nodes = (date(2024, 6, 29), date(2025, 12, 29), date(2028, 12, 29), date(2053, 12, 29))
    return SolvedCurve(AS_OF, nodes, tuple(zero for _ in nodes))


def test_solved_curve_discount_basics() -> None:
    curve = _flat_curve(0.05)
    assert curve.discount(AS_OF) == 1.0
    assert curve.discount(date(2025, 12, 29)) < curve.discount(date(2024, 6, 29)) < 1.0
    with pytest.raises(MultiCurveError):
        curve.discount(date(2023, 1, 1))
    with pytest.raises(MultiCurveError):
        SolvedCurve(AS_OF, (date(2024, 1, 1), date(2024, 1, 1)), (0.05, 0.05))


def test_simultaneous_two_curve_solve() -> None:
    """OIS-discounted projection curve: solve discount + projection together, all reprice."""
    discount_quotes = [
        MarketQuote(DepositHelper(t, USA), r)
        for t, r in [("3M", 0.0525), ("6M", 0.0510)]
    ] + [
        MarketQuote(OisHelper(t, USA), r)
        for t, r in [("1Y", 0.0475), ("2Y", 0.0430), ("5Y", 0.0395), ("10Y", 0.0390)]
    ]
    projection_quotes = [
        MarketQuote(DepositHelper(t, USA, on_projection=True), r)
        for t, r in [("3M", 0.0560), ("6M", 0.0548)]
    ] + [
        MarketQuote(SwapHelper(t, USA, float_frequency_months=3), r)
        for t, r in [("1Y", 0.0505), ("2Y", 0.0462), ("5Y", 0.0430), ("10Y", 0.0425)]
    ]
    curve_set = build_curve_set(
        as_of=AS_OF,
        calendar=USA,
        discount_quotes=discount_quotes,
        projection_quotes=projection_quotes,
    )
    assert not curve_set.is_single_curve
    worst = max(
        abs(
            q.helper.reprice_residual(q.quote, curve_set.discount, curve_set.projection)
        )
        for q in [*discount_quotes, *projection_quotes]
    )
    assert worst < 1e-8
    # Projection carries a positive basis over discount, so its discount factors differ.
    assert curve_set.projection.discount(date(2028, 12, 29)) != pytest.approx(
        curve_set.discount.discount(date(2028, 12, 29))
    )


def test_build_curve_set_requires_ascending_and_distinct_pillars() -> None:
    quotes = [
        MarketQuote(SwapHelper("2Y", USA), 0.043),
        MarketQuote(SwapHelper("1Y", USA), 0.048),  # out of order
    ]
    with pytest.raises(MultiCurveError):
        build_curve_set(as_of=AS_OF, calendar=USA, discount_quotes=quotes)


def test_forward_grid_shape_and_stub() -> None:
    curve = _flat_curve(0.04)
    grid = forward_grid(
        curve, as_of=AS_OF, curve_frequency_months=3, calendar=USA, periods=8
    )
    assert len(grid.rows) == 9
    stub = grid.rows[0]
    assert stub.start == AS_OF and stub.end == date(2023, 12, 31)
    assert stub.discount_factor == 1.0 and stub.yield_ == 0.0
    # Boundaries chain; period DF equals the curve's DF ratio; yields are positive & near flat.
    for previous, row in zip(grid.rows, grid.rows[1:], strict=False):
        assert row.start == previous.end
        assert row.discount_factor == pytest.approx(
            curve.discount(row.end) / curve.discount(row.start)
        )
        assert 0.0 < row.yield_ < 0.05


def test_forward_grid_monthly_anchor() -> None:
    grid = forward_grid(
        _flat_curve(0.04), as_of=AS_OF, curve_frequency_months=1, calendar=USA, periods=3
    )
    assert [r.end for r in grid.rows] == [
        date(2023, 12, 31),
        date(2024, 1, 31),
        date(2024, 2, 29),
        date(2024, 3, 29),  # 2024-03-31 Sun rolls back (MF) to Good Friday, a US business day
    ]


def test_forward_grid_for_frequency_builds_business_day_daily_periods() -> None:
    rows = forward_grid_for_frequency(
        _flat_curve(0.04),
        as_of=AS_OF,
        frequency="1D",
        calendar=USA,
        periods=3,
    )
    assert len(rows) == 4  # spot stub + three business-day periods
    assert rows[0].start == rows[0].end == AS_OF
    assert [row.end for row in rows[1:]] == [
        date(2024, 1, 2),
        date(2024, 1, 3),
        date(2024, 1, 4),
    ]
    assert all(0 < row.discount_factor <= 1 for row in rows[1:])


def test_convert_basis_ordering() -> None:
    curve = _flat_curve(0.05)
    start, end = date(2024, 3, 20), date(2024, 6, 20)
    mm360 = convert_basis(curve, start, end, DayCount.ACT_360)
    mm365 = convert_basis(curve, start, end, DayCount.ACT_365F)
    thirty = convert_basis(curve, start, end, DayCount.THIRTY_360)
    # Same discount factors; f = (DF_ratio-1)/tau, and tau_360 > tau_365, so the Act/360 rate
    # is the smaller one. 30/360 uses a 90-day quarter (smaller tau) so it prints highest.
    assert mm360 < mm365
    assert thirty > mm360
    assert thirty == pytest.approx(mm360, rel=5e-2)  # within ~2% over a quarter


def test_forward_grid_digest_is_value_based() -> None:
    curve = _flat_curve(0.04)
    kwargs = {
        "as_of": AS_OF,
        "curve_frequency_months": 3,
        "calendar": USA,
        "periods": 8,
        "convention": BusinessDayConvention.MODIFIED_FOLLOWING,
    }
    a = forward_grid(curve, **kwargs)
    b = forward_grid(curve, **kwargs)
    assert a.input_digest == b.input_digest
    assert len(a.input_digest) == 64
    c = forward_grid(_flat_curve(0.045), **kwargs)
    assert c.input_digest != a.input_digest
