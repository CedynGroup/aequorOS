"""Hand-verified golden tests for the FTP contingent-liquidity charge.

``contingent_liquidity_charges`` prices the pre-funded stressed draw on
committed facilities (LRMD 2026 ¶78–79): each facility family pays
``undrawn × expected_draw × carry``, where the carry is the FTP curve's
term-minus-overnight spread at the buffer tenor, floored at zero so an
inverted curve never turns the charge into a rebate. Every expectation
below is derived by hand from that contract with explicit Decimal
arithmetic, independently of the engine, and the case set is chosen so a
removed floor or a reversed interpolation weight cannot survive: the
floor case uses a strictly inverted bracket and the interpolation case
prices a quarter-point (non-midpoint) buffer tenor where the reversed
weights give a different answer.
"""

from __future__ import annotations

from decimal import Decimal

from app.ftp.domain.engine import (
    ContingentFacility,
    CurvePoint,
    build_curve,
    contingent_liquidity_charges,
)

M = Decimal("1000000")


def _point(label: str, tenor: str, base: str, liquidity_bps: str, funding_bps: str) -> CurvePoint:
    """A curve point whose ftp rate is base + (liquidity + funding) / 100."""
    ftp = Decimal(base) + (Decimal(liquidity_bps) + Decimal(funding_bps)) / Decimal("100")
    return CurvePoint(
        tenor_label=label,
        tenor_years=Decimal(tenor),
        base_yield_pct=Decimal(base),
        liquidity_premium_bps=Decimal(liquidity_bps),
        funding_spread_bps=Decimal(funding_bps),
        ftp_rate_pct=ftp,
    )


def _normal_curve() -> list[CurvePoint]:
    # A normal (upward-sloping) curve: overnight 25.00%, 1y 27.60%, 2y 28.50%.
    return [
        _point("overnight", "0.003", "24.6", "0", "40"),
        _point("1y", "1.0", "27.0", "0", "60"),
        _point("2y", "2.0", "28.0", "0", "50"),
    ]


def test_normal_curve_charges_each_family_the_positive_carry() -> None:
    curve = build_curve(_normal_curve())

    # carry = rate_at(1y) - overnight = 27.60 - 25.00 = 2.60 (default 1y buffer).
    result = contingent_liquidity_charges(
        [
            # Input deliberately unsorted; the engine prices families by line code.
            ContingentFacility(
                line_code="trade_letters_of_credit",
                undrawn_amount=Decimal("4000000"),
                expected_draw_pct=Decimal("35"),
            ),
            ContingentFacility(
                line_code="performance_guarantees",
                undrawn_amount=Decimal("2500000"),
                expected_draw_pct=Decimal("60"),
            ),
        ],
        curve,
    )

    assert result.buffer_cost_pct == Decimal("2.6")
    # trade_letters_of_credit: 4,000,000 x 35/100 x 2.60/100 = 1,400,000 x 0.026.
    # performance_guarantees: 2,500,000 x 60/100 x 2.60/100 = 1,500,000 x 0.026.
    assert [(item.line_code, item.annual_charge) for item in result.items] == [
        ("performance_guarantees", Decimal("39000.0000")),
        ("trade_letters_of_credit", Decimal("36400.0000")),
    ]
    assert result.items[1].undrawn_amount == Decimal("4000000.0000")
    assert result.items[1].expected_draw_pct == Decimal("35")
    # 39,000 + 36,400 = 75,400.
    assert result.total_charge == Decimal("75400.0000")


def test_inverted_curve_floors_the_carry_at_zero() -> None:
    # Term rates below overnight: the raw carry is 27.00 - 30.00 = -3.00, and
    # the floor must keep the charge at zero rather than pay a rebate.
    curve = build_curve(
        [
            _point("overnight", "0.003", "29.6", "0", "40"),
            _point("1y", "1.0", "26.5", "0", "50"),
        ]
    )

    result = contingent_liquidity_charges(
        [
            ContingentFacility(
                line_code="import_credit_line",
                undrawn_amount=Decimal("1000000"),
                expected_draw_pct=Decimal("50"),
            )
        ],
        curve,
    )

    # Without the floor the family would be charged
    # 1,000,000 x 50/100 x -3.00/100 = -15,000 (a rebate on the stressed draw).
    assert result.buffer_cost_pct == Decimal("0")
    assert [
        (item.line_code, item.buffer_cost_pct, item.annual_charge) for item in result.items
    ] == [("import_credit_line", Decimal("0"), Decimal("0.0000"))]
    assert result.total_charge == Decimal("0.0000")


def test_empty_facilities_leave_no_charge() -> None:
    curve = build_curve(_normal_curve())

    result = contingent_liquidity_charges([], curve)

    assert result.items == ()
    # The carry is still derived from the curve even with nothing to price.
    assert result.buffer_cost_pct == Decimal("2.6")
    assert result.total_charge == Decimal("0.0000")


def _historical_curve() -> list[CurvePoint]:
    """The held August 2026 Ghana series as an illustrative FTP proxy curve.

    Historically calibrated from the market-desk harvest captured 2026-08-09
    (a historical snapshot, not current data; per-artifact source URL + SHA-256
    recorded in ``backend/tests/fixtures/market_desk/manifest.json``):

    - overnight 10.23: BoG daily weighted-average interbank rate,
      ``market_desk/series/interbank_rate.csv`` row ``2026-08-07,10.23``
      (BoG table 69; manifest sha256
      ``c9588f9feb62fae4c8ad30633795df728ea2cbb95a21bafdf316c3907d70c092``).
    - 91d 5.7618 / 182d 7.6409 / 364d 12.9821: GoG treasury-bill tender
      results, ``market_desk/series/tbill_rates.csv`` rows ``2026-08-03,
      tender 2018``, INTEREST-rate column (BoG table 2; manifest sha256
      ``4ccbe261a6e620381cee674f587e2a836e6a16d94de1df94a1520c4baad2ce26``).
      The same rows' discount column (5.6800 / 7.3597 / 11.4904) is a
      different rate convention and is deliberately not mixed in.
    """
    return [
        _point("overnight", "0.003", "10.23", "0", "0"),
        _point("91d", "0.25", "5.7618", "0", "0"),
        _point("182d", "0.5", "7.6409", "0", "0"),
        _point("364d", "1.0", "12.9821", "0", "0"),
    ]


def test_historical_ghana_series_curve_calibrates_the_charge() -> None:
    """The held August 2026 Ghana series prices carry, floor and interpolation.

    Expectations are hand-derived Decimal arithmetic from the published rates
    above, independently of the engine:

    - Default 1y buffer: carry = 12.9821 - 10.23 = 2.7521, and the charge is
      8,000,000 x 40/100 x 2.7521/100 = 88,067.20.
    - 91d buffer: the historical front was inverted (91-day 5.7618 below the
      10.23 overnight), so carry = -4.4682 and the floor binds at zero
      (without it: 8,000,000 x 40/100 x -4.4682/100 = -142,982.40, a rebate).
    - 0.8y buffer: 0.6 of the way up the 182d..364d bracket,
      7.6409 + 0.6 x (12.9821 - 7.6409) = 10.84562, exact at the engine's
      6-decimal rate quantum, so carry = 10.845620 - 10.23 = 0.615620 and the
      charge is 8,000,000 x 40/100 x 0.615620/100 = 19,699.84. A reversed
      interpolation weight (0.4) would price 9.777380, below the overnight
      rate, and floor to zero — this case cannot survive a reversed weight
      either.

    Stated limitations: the sovereign bill yields are a risk-free proxy, not
    an actual bank FTP funding cost — no funding spread, basis, compounding
    or day-count conversion is applied, and the bill tenors are rounded to
    curve tenors (91d -> 0.25y, 182d -> 0.5y, 364d -> 1.0y). The overnight
    point is a different instrument (unsecured interbank) on a different date
    (2026-08-07) than the bill rows (2026-08-03); both are used as published.
    The published yields are used wholesale as the FTP rate (zero liquidity
    and funding components) — no base/liquidity/funding decomposition is
    claimed, and the curve shape is the data's own, inverted at the front.
    Facility amounts, the 40% draw rate and the 0.8y buffer tenor are
    synthetic round choices for exact arithmetic. No claim that these points
    are representative of any actual bank's FTP curve or of current rates.
    """
    curve = build_curve(_historical_curve())

    # Default 1y buffer tenor: term 12.9821 vs overnight 10.23.
    result = contingent_liquidity_charges(
        [
            ContingentFacility(
                line_code="trade_letters_of_credit",
                undrawn_amount=Decimal("8000000"),
                expected_draw_pct=Decimal("40"),
            )
        ],
        curve,
    )

    assert result.buffer_cost_pct == Decimal("2.7521")
    # 8,000,000 x 40/100 x 2.7521/100 = 3,200,000 x 0.027521.
    assert result.items[0].annual_charge == Decimal("88067.2000")
    assert result.total_charge == Decimal("88067.2000")

    # The same family at a 91-day buffer tenor: the floor binds on the
    # historically inverted front of the curve.
    short_result = contingent_liquidity_charges(
        [
            ContingentFacility(
                line_code="trade_letters_of_credit",
                undrawn_amount=Decimal("8000000"),
                expected_draw_pct=Decimal("40"),
            )
        ],
        curve,
        buffer_tenor_years=Decimal("0.25"),
    )

    assert short_result.buffer_cost_pct == Decimal("0")
    assert short_result.items[0].annual_charge == Decimal("0.0000")
    assert short_result.total_charge == Decimal("0.0000")

    # The same family at a 0.8y buffer tenor: interpolated inside the
    # 182d..364d bracket, just above the overnight rate.
    mid_result = contingent_liquidity_charges(
        [
            ContingentFacility(
                line_code="trade_letters_of_credit",
                undrawn_amount=Decimal("8000000"),
                expected_draw_pct=Decimal("40"),
            )
        ],
        curve,
        buffer_tenor_years=Decimal("0.8"),
    )

    assert mid_result.buffer_cost_pct == Decimal("0.615620")
    # 8,000,000 x 40/100 x 0.615620/100 = 3,200,000 x 0.0061562.
    assert mid_result.items[0].annual_charge == Decimal("19699.8400")
    assert mid_result.total_charge == Decimal("19699.8400")


def test_buffer_tenor_uses_non_midpoint_interpolation() -> None:
    curve = build_curve(
        [
            _point("overnight", "0.003", "24.6", "0", "40"),
            _point("1y", "1.0", "27.0", "0", "60"),
            _point("3y", "3.0", "28.7", "0", "55"),
        ]
    )

    # A 1.5y buffer tenor prices a quarter of the way up the 1y..3y bracket:
    # rate = 27.60 + (1.5 - 1)/(3 - 1) x (29.25 - 27.60) = 27.60 + 0.4125
    # = 28.0125, so carry = 28.0125 - 25.00 = 3.0125. The quarter-point weight
    # matters: a reversed interpolation fraction (0.75 instead of 0.25) would
    # price 28.8375 and a midpoint would hide the difference.
    result = contingent_liquidity_charges(
        [
            ContingentFacility(
                line_code="committed_term_loan",
                undrawn_amount=Decimal("10000000"),
                expected_draw_pct=Decimal("50"),
            )
        ],
        curve,
        buffer_tenor_years=Decimal("1.5"),
    )

    # 10,000,000 x 50/100 x 3.0125/100 = 5,000,000 x 0.030125.
    assert result.buffer_cost_pct == Decimal("3.0125")
    assert result.items[0].annual_charge == Decimal("150625.0000")
    assert result.total_charge == Decimal("150625.0000")
