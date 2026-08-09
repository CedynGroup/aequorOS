"""Golden-value tests for day-count and quote conventions.

The discount<->interest goldens are REAL published BoG auction results
(03 Aug 2026): the implementation must reproduce the published interest rate
from the published discount rate to 4 decimal places on ACT/364 — these three
pairs are the anchor proof that the conversion formula matches the market's.
"""

from __future__ import annotations

import math
from datetime import date

import pytest

from app.domain.curves.conventions import (
    Compounding,
    ConventionError,
    DayCount,
    accrued_interest,
    bond_cashflows,
    bond_price,
    bond_ytm,
    convert_zero_rate,
    discount_factor_to_zero,
    discount_to_yield,
    year_fraction,
    yield_to_discount,
    zero_to_discount_factor,
)

# BoG published auction results, 03 Aug 2026: (tenor days, discount %, interest %).
BOG_PUBLISHED_PAIRS = (
    (91, 5.6800, 5.7618),
    (182, 7.3597, 7.6409),
    (364, 11.4904, 12.9821),
)


class TestYearFraction:
    def test_act_364_full_year(self) -> None:
        assert year_fraction(date(2026, 8, 3), date(2027, 8, 2), DayCount.ACT_364) == 1.0

    def test_bases_disagree_on_same_span(self) -> None:
        start, end = date(2026, 8, 3), date(2027, 8, 2)
        assert year_fraction(start, end, DayCount.ACT_365F) == 364 / 365
        assert year_fraction(start, end, DayCount.ACT_360) == 364 / 360

    def test_reversed_dates_raise(self) -> None:
        with pytest.raises(ConventionError):
            year_fraction(date(2026, 8, 4), date(2026, 8, 3), DayCount.ACT_364)


class TestBogDiscountYieldGoldens:
    """The three real BoG pairs, reproduced to 4 dp in both directions."""

    @pytest.mark.parametrize(("days", "discount_pct", "interest_pct"), BOG_PUBLISHED_PAIRS)
    def test_discount_to_yield_reproduces_published_interest(
        self, days: int, discount_pct: float, interest_pct: float
    ) -> None:
        computed = discount_to_yield(discount_pct / 100.0, days) * 100.0
        assert round(computed, 4) == pytest.approx(interest_pct, abs=5e-5)

    @pytest.mark.parametrize(("days", "discount_pct", "interest_pct"), BOG_PUBLISHED_PAIRS)
    def test_yield_to_discount_reproduces_published_discount(
        self, days: int, discount_pct: float, interest_pct: float
    ) -> None:
        computed = yield_to_discount(interest_pct / 100.0, days) * 100.0
        # The published interest is itself rounded to 4 dp, so allow 1 unit in
        # the 4th decimal on the way back.
        assert computed == pytest.approx(discount_pct, abs=1e-4)

    @pytest.mark.parametrize(("days", "discount_pct", "_"), BOG_PUBLISHED_PAIRS)
    def test_round_trip_is_exact(self, days: int, discount_pct: float, _: float) -> None:
        d = discount_pct / 100.0
        assert yield_to_discount(discount_to_yield(d, days), days) == pytest.approx(d, abs=1e-15)

    def test_degenerate_inputs_raise(self) -> None:
        with pytest.raises(ConventionError):
            discount_to_yield(0.05, 0)
        with pytest.raises(ConventionError):
            discount_to_yield(1.5, 364)  # price would be negative


class TestCompoundingConversions:
    def test_annual_to_continuous_golden(self) -> None:
        # 10% annually compounded == ln(1.1) continuously compounded, any t.
        assert convert_zero_rate(
            0.10, 5.0, Compounding.ANNUAL, Compounding.CONTINUOUS
        ) == pytest.approx(math.log(1.1), abs=1e-15)

    def test_simple_discount_factor_golden(self) -> None:
        # 5% simple over 2 years discounts by 1 / 1.10.
        assert zero_to_discount_factor(0.05, 2.0, Compounding.SIMPLE) == pytest.approx(
            1.0 / 1.10, abs=1e-15
        )

    @pytest.mark.parametrize(
        "compounding", [Compounding.SIMPLE, Compounding.ANNUAL, Compounding.CONTINUOUS]
    )
    def test_df_zero_round_trip(self, compounding: Compounding) -> None:
        rate, t = 0.1234, 3.7
        df = zero_to_discount_factor(rate, t, compounding)
        assert discount_factor_to_zero(df, t, compounding) == pytest.approx(rate, abs=1e-14)

    def test_t_zero_df_is_one(self) -> None:
        assert zero_to_discount_factor(0.25, 0.0, Compounding.CONTINUOUS) == 1.0

    def test_invalid_df_raises(self) -> None:
        with pytest.raises(ConventionError):
            discount_factor_to_zero(0.0, 1.0, Compounding.ANNUAL)


class TestBondPriceYtm:
    SETTLEMENT = date(2026, 1, 15)
    MATURITY = date(2028, 1, 15)

    def test_par_bond_prices_at_par(self) -> None:
        # Settlement on a coupon date, coupon == yield, exact 365-day periods.
        price = bond_price(0.10, self.SETTLEMENT, self.MATURITY, 0.10, 1, DayCount.ACT_365F)
        assert price == pytest.approx(100.0, abs=1e-10)

    def test_hand_computed_price_golden(self) -> None:
        # 2y 10% annual at 12%: 10/1.12 + 110/1.12^2 = 96.61989795918...
        price = bond_price(0.12, self.SETTLEMENT, self.MATURITY, 0.10, 1, DayCount.ACT_365F)
        assert price == pytest.approx(10.0 / 1.12 + 110.0 / 1.12**2, abs=1e-10)
        assert price == pytest.approx(96.6198979591837, abs=1e-10)

    def test_accrued_interest_golden(self) -> None:
        # 90 days into a 365-day annual period: 10 * 90/365 = 2.46575342...
        accrued = accrued_interest(date(2026, 4, 15), self.MATURITY, 0.10, 1, DayCount.ACT_365F)
        assert accrued == pytest.approx(10.0 * 90.0 / 365.0, abs=1e-12)
        assert accrued == pytest.approx(2.4657534246575343, abs=1e-12)

    @pytest.mark.parametrize("frequency", [1, 2])
    @pytest.mark.parametrize("ytm", [0.02, 0.10, 0.1480, 0.35])
    def test_price_ytm_round_trip(self, frequency: int, ytm: float) -> None:
        price = bond_price(
            ytm, date(2026, 8, 3), date(2036, 8, 3), 0.0985, frequency, DayCount.ACT_365F
        )
        recovered = bond_ytm(
            price, date(2026, 8, 3), date(2036, 8, 3), 0.0985, frequency, DayCount.ACT_365F
        )
        assert recovered == pytest.approx(ytm, abs=1e-10)

    def test_dirty_round_trip_mid_period(self) -> None:
        settlement = date(2026, 4, 15)  # between coupons: accrued is non-zero
        dirty = bond_price(
            0.13, settlement, self.MATURITY, 0.10, 1, DayCount.ACT_365F, clean=False
        )
        clean = bond_price(0.13, settlement, self.MATURITY, 0.10, 1, DayCount.ACT_365F)
        assert dirty - clean == pytest.approx(
            accrued_interest(settlement, self.MATURITY, 0.10, 1, DayCount.ACT_365F), abs=1e-12
        )
        assert bond_ytm(
            dirty, settlement, self.MATURITY, 0.10, 1, DayCount.ACT_365F, clean=False
        ) == pytest.approx(0.13, abs=1e-10)

    def test_extreme_price_uses_bisection_bracket(self) -> None:
        # Deep-distress price implies a ~370% yield — still solved, still exact.
        ytm = bond_ytm(5.0, self.SETTLEMENT, self.MATURITY, 0.10, 1, DayCount.ACT_365F)
        assert bond_price(
            ytm, self.SETTLEMENT, self.MATURITY, 0.10, 1, DayCount.ACT_365F
        ) == pytest.approx(5.0, abs=1e-9)

    def test_unattainable_price_raises(self) -> None:
        with pytest.raises(ConventionError):
            bond_ytm(1e9, self.SETTLEMENT, self.MATURITY, 0.10, 1, DayCount.ACT_365F)

    def test_cashflow_schedule_semiannual(self) -> None:
        flows = bond_cashflows(
            date(2026, 8, 3), date(2028, 8, 3), 0.0835, 2, DayCount.ACT_365F
        )
        assert [flow.payment_date for flow in flows] == [
            date(2027, 2, 3),
            date(2027, 8, 3),
            date(2028, 2, 3),
            date(2028, 8, 3),
        ]
        assert flows[0].amount == pytest.approx(4.175)
        assert flows[-1].amount == pytest.approx(104.175)

    def test_invalid_frequency_raises(self) -> None:
        with pytest.raises(ConventionError):
            bond_cashflows(self.SETTLEMENT, self.MATURITY, 0.10, 4, DayCount.ACT_365F)

    def test_matured_bond_raises(self) -> None:
        with pytest.raises(ConventionError):
            bond_price(0.1, self.MATURITY, self.MATURITY, 0.10, 1, DayCount.ACT_365F)
