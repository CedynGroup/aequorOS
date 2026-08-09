"""Bootstrap: exact bill discount factors, exact bond repricing (the acid test),
duplicate collapsing, and input validation.

The bill quotes are the REAL BoG 03 Aug 2026 auction discount rates; bonds are
synthetic (documented in test_integration_ghana). The acid test — every input
instrument reprices on the finished curve within 1e-8 — runs across all four
interpolation methods, which also exercises the refinement sweeps that
non-local methods (monotone convex, PCHIP) require.
"""

from __future__ import annotations

import math
from datetime import date

import pytest

from app.domain.curves.bootstrap import (
    BillQuote,
    BondQuote,
    BootstrapError,
    ZeroCurve,
    bootstrap_zero_curve,
)
from app.domain.curves.conventions import bond_cashflows

SETTLEMENT = date(2026, 8, 3)

# Real BoG 03 Aug 2026 auction: 91/182/364-day discount rates.
BILLS = (
    BillQuote(settlement=SETTLEMENT, maturity=date(2026, 11, 2), discount_rate=0.056800),
    BillQuote(settlement=SETTLEMENT, maturity=date(2027, 2, 1), discount_rate=0.073597),
    BillQuote(settlement=SETTLEMENT, maturity=date(2027, 8, 2), discount_rate=0.114904),
)
BONDS = (
    BondQuote(
        settlement=SETTLEMENT, maturity=date(2028, 8, 3), coupon_rate=0.0835, frequency=2,
        price=91.90,
    ),
    BondQuote(
        settlement=SETTLEMENT, maturity=date(2031, 2, 3), coupon_rate=0.0850, frequency=2,
        price=81.50,
    ),
    BondQuote(
        settlement=SETTLEMENT, maturity=date(2033, 8, 3), coupon_rate=0.0880, frequency=2,
        price=76.00,
    ),
    BondQuote(
        settlement=SETTLEMENT, maturity=date(2036, 8, 3), coupon_rate=0.0985, frequency=2,
        price=74.50,
    ),
)


def _bond_model_price(curve: ZeroCurve, bond: BondQuote) -> float:
    flows = bond_cashflows(
        bond.settlement, bond.maturity, bond.coupon_rate, bond.frequency, bond.day_count
    )
    return sum(flow.amount * curve.df(curve.time_of(flow.payment_date)) for flow in flows)


class TestBillsOnly:
    def test_bill_discount_factors_are_exact(self) -> None:
        curve = bootstrap_zero_curve(BILLS)
        for bill in BILLS:
            t = curve.time_of(bill.maturity)
            assert curve.df(t) == pytest.approx(bill.discount_factor(), abs=1e-14)

    def test_91_day_node_zero_golden(self) -> None:
        # DF = 1 - d*t = 1 - 0.0568*0.25 = 0.98580 exactly;
        # CC zero = -ln(0.98580)/0.25.
        curve = bootstrap_zero_curve(BILLS)
        assert curve.df(0.25) == pytest.approx(0.98580, abs=1e-14)
        assert curve.zero(0.25) == pytest.approx(-math.log(0.98580) / 0.25, abs=1e-14)

    def test_yield_quoted_bill_equivalent_to_discount_quoted(self) -> None:
        # Quoting the same bill as its true yield must give the same DF.
        discount_quoted = BILLS[0]
        yield_quoted = BillQuote(
            settlement=SETTLEMENT,
            maturity=discount_quoted.maturity,
            yield_rate=discount_quoted.true_yield(),
        )
        assert yield_quoted.discount_factor() == pytest.approx(
            discount_quoted.discount_factor(), abs=1e-15
        )


class TestAcidRepricing:
    @pytest.mark.parametrize(
        "interpolation", ["monotone_convex", "pchip", "linear_zero", "log_linear_df"]
    )
    def test_every_instrument_reprices_within_1e8(self, interpolation: str) -> None:
        curve = bootstrap_zero_curve(BILLS, BONDS, interpolation=interpolation)
        for bill in BILLS:
            t = curve.time_of(bill.maturity)
            assert curve.df(t) == pytest.approx(bill.discount_factor(), abs=1e-8)
        for bond in BONDS:
            assert _bond_model_price(curve, bond) == pytest.approx(
                bond.dirty_price(), abs=1e-8
            )

    def test_nodes_are_maturity_ordered_and_complete(self) -> None:
        curve = bootstrap_zero_curve(BILLS, BONDS)
        assert len(curve.times) == len(BILLS) + len(BONDS)
        assert list(curve.times) == sorted(curve.times)


class TestZeroCurveObject:
    def test_immutable(self) -> None:
        curve = bootstrap_zero_curve(BILLS)
        with pytest.raises(AttributeError):
            curve.interpolation = "pchip"  # type: ignore[misc]

    def test_df_zero_at_origin(self) -> None:
        curve = bootstrap_zero_curve(BILLS)
        assert curve.df(0.0) == 1.0

    def test_forward_consistent_with_zeros(self) -> None:
        curve = bootstrap_zero_curve(BILLS, BONDS)
        t1, t2 = 1.0, 3.0
        expected = (curve.zero(t2) * t2 - curve.zero(t1) * t1) / (t2 - t1)
        assert curve.forward(t1, t2) == pytest.approx(expected, abs=1e-15)
        # forward over [t1,t2] also equals ln(df(t1)/df(t2)) / (t2-t1)
        assert curve.forward(t1, t2) == pytest.approx(
            math.log(curve.df(t1) / curve.df(t2)) / (t2 - t1), abs=1e-12
        )

    def test_forward_argument_order_enforced(self) -> None:
        curve = bootstrap_zero_curve(BILLS)
        with pytest.raises(BootstrapError):
            curve.forward(2.0, 1.0)


class TestDuplicateCollapsing:
    def test_volume_weight_blends_bill_yields(self) -> None:
        heavy = BillQuote(
            settlement=SETTLEMENT, maturity=date(2026, 11, 2), yield_rate=0.060, volume=300.0
        )
        light = BillQuote(
            settlement=SETTLEMENT, maturity=date(2026, 11, 2), yield_rate=0.056, volume=100.0
        )
        curve = bootstrap_zero_curve([heavy, light], duplicate_policy="volume_weight")
        blended = 0.75 * 0.060 + 0.25 * 0.056
        expected_df = 1.0 / (1.0 + blended * 0.25)
        assert curve.df(0.25) == pytest.approx(expected_df, abs=1e-14)

    def test_missing_volume_falls_back_to_equal_weights(self) -> None:
        a = BillQuote(settlement=SETTLEMENT, maturity=date(2026, 11, 2), yield_rate=0.060)
        b = BillQuote(
            settlement=SETTLEMENT, maturity=date(2026, 11, 2), yield_rate=0.056, volume=100.0
        )
        curve = bootstrap_zero_curve([a, b], duplicate_policy="volume_weight")
        expected_df = 1.0 / (1.0 + 0.058 * 0.25)
        assert curve.df(0.25) == pytest.approx(expected_df, abs=1e-14)

    def test_last_wins_keeps_final_quote(self) -> None:
        first = BillQuote(settlement=SETTLEMENT, maturity=date(2026, 11, 2), yield_rate=0.060)
        second = BillQuote(settlement=SETTLEMENT, maturity=date(2026, 11, 2), yield_rate=0.056)
        curve = bootstrap_zero_curve([first, second], duplicate_policy="last_wins")
        assert curve.df(0.25) == pytest.approx(second.discount_factor(), abs=1e-14)

    def test_duplicate_bonds_volume_weight_prices(self) -> None:
        quote_a = BondQuote(
            settlement=SETTLEMENT, maturity=date(2028, 8, 3), coupon_rate=0.0835, frequency=2,
            price=91.00, volume=100.0,
        )
        quote_b = BondQuote(
            settlement=SETTLEMENT, maturity=date(2028, 8, 3), coupon_rate=0.0835, frequency=2,
            price=93.00, volume=300.0,
        )
        curve = bootstrap_zero_curve(BILLS, [quote_a, quote_b])
        blended = BondQuote(
            settlement=SETTLEMENT, maturity=date(2028, 8, 3), coupon_rate=0.0835, frequency=2,
            price=0.25 * 91.00 + 0.75 * 93.00,
        )
        assert _bond_model_price(curve, blended) == pytest.approx(
            blended.dirty_price(), abs=1e-8
        )

    def test_duplicate_bonds_with_different_terms_raise(self) -> None:
        quote_a = BondQuote(
            settlement=SETTLEMENT, maturity=date(2028, 8, 3), coupon_rate=0.0835, frequency=2,
            price=91.00, volume=100.0,
        )
        quote_b = BondQuote(
            settlement=SETTLEMENT, maturity=date(2028, 8, 3), coupon_rate=0.0900, frequency=2,
            price=93.00, volume=300.0,
        )
        with pytest.raises(BootstrapError, match="coupon terms"):
            bootstrap_zero_curve(BILLS, [quote_a, quote_b])


class TestValidation:
    def test_no_instruments_raise(self) -> None:
        with pytest.raises(BootstrapError):
            bootstrap_zero_curve([])

    def test_mixed_settlements_raise(self) -> None:
        other = BillQuote(
            settlement=date(2026, 8, 4), maturity=date(2026, 11, 3), discount_rate=0.0568
        )
        with pytest.raises(BootstrapError, match="settlement"):
            bootstrap_zero_curve([BILLS[0], other])

    def test_bill_quote_needs_exactly_one_rate(self) -> None:
        with pytest.raises(BootstrapError):
            BillQuote(settlement=SETTLEMENT, maturity=date(2026, 11, 2))
        with pytest.raises(BootstrapError):
            BillQuote(
                settlement=SETTLEMENT, maturity=date(2026, 11, 2),
                discount_rate=0.05, yield_rate=0.05,
            )

    def test_bond_inside_bill_span_raises(self) -> None:
        early_bond = BondQuote(
            settlement=SETTLEMENT, maturity=date(2027, 2, 1), coupon_rate=0.08, frequency=2,
            price=95.0,
        )
        with pytest.raises(BootstrapError, match="does not extend"):
            bootstrap_zero_curve(BILLS, [early_bond])

    def test_unsolvable_bond_price_raises(self) -> None:
        absurd = BondQuote(
            settlement=SETTLEMENT, maturity=date(2028, 8, 3), coupon_rate=0.0835, frequency=2,
            price=2000.0,  # far above the sum of undiscounted cash flows
        )
        with pytest.raises(BootstrapError):
            bootstrap_zero_curve(BILLS, [absurd])
