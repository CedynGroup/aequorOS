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
