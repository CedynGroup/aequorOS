"""End-to-end Ghana curve pipeline: real bills -> bootstrap -> QA -> fallback -> OIS.

Instrument fixture
------------------
- **Bills (REAL)**: BoG auction results of 03 Aug 2026 — 91-day discount
  5.6800%, 182-day 7.3597%, 364-day 11.4904% (the same published pairs the
  conventions goldens pin: true yields 5.7618 / 7.6409 / 12.9821).
- **Bonds (SYNTHETIC, documented)**: four GoG bonds with plausible post-DDEP
  terms — semi-annual coupons in the DDEP restructured range (8.35-9.85%) and
  deep-discount clean prices implying YTMs of 13.06% (2y), 14.18% (4.5y),
  14.33% (7y) and 14.80% (10y): below-par pricing consistent with a market
  yielding well above the restructured coupon stack.
- **Policy fixture**: MPR 8.00% and six synthetic 2026 MPC meeting dates with
  a -170 bps overnight spread (the late-2025 excess-liquidity figure quoted in
  the spec). Test fixtures, not market assertions.

The final assertion is the platform's reproducibility invariant: running the
whole pipeline twice produces byte-identical CurveBuildResults and digests.
"""

from __future__ import annotations

import math
from datetime import date

import pytest

from app.domain.curves.bootstrap import (
    BillQuote,
    BondQuote,
    ZeroCurve,
    bootstrap_zero_curve,
)
from app.domain.curves.conventions import DayCount, bond_cashflows, discount_to_yield
from app.domain.curves.forwards import ForwardQaResult, qa_forwards
from app.domain.curves.nss import NssFit, fit_nss, nss_zero
from app.domain.curves.objects import (
    CurveBuildResult,
    CurveDefinition,
    CurveNodes,
)
from app.domain.curves.ois_step import MeetingDateStepCurve

SETTLEMENT = date(2026, 8, 3)

# Real BoG 03 Aug 2026 auction discount rates (percent / 100).
BILL_QUOTES = (
    (date(2026, 11, 2), 0.056800),  # 91-day
    (date(2027, 2, 1), 0.073597),  # 182-day
    (date(2027, 8, 2), 0.114904),  # 364-day
)
# Synthetic post-DDEP GoG bonds: (maturity, semi-annual coupon, clean price).
BOND_QUOTES = (
    (date(2028, 8, 3), 0.0835, 91.90),  # 2y, YTM ~13.06%
    (date(2031, 2, 3), 0.0850, 81.50),  # 4.5y, YTM ~14.18%
    (date(2033, 8, 3), 0.0880, 76.00),  # 7y, YTM ~14.33%
    (date(2036, 8, 3), 0.0985, 74.50),  # 10y, YTM ~14.80%
)
# Policy fixture: MPR 8.00%, six synthetic 2026 MPC dates (bimonthly, the BoG
# cadence), calibrated overnight spread -170 bps. Meetings before the curve
# anchor cannot step the forward — only Sep and Nov remain live here.
MPR = 0.08
MPC_DATES_2026 = (
    date(2026, 1, 26),
    date(2026, 3, 30),
    date(2026, 5, 25),
    date(2026, 7, 27),
    date(2026, 9, 28),
    date(2026, 11, 23),
)
OVERNIGHT_SPREAD_BPS = -170.0
QA_GRID = tuple(0.1 * i for i in range(1, 101))


def _bills() -> list[BillQuote]:
    return [
        BillQuote(settlement=SETTLEMENT, maturity=maturity, discount_rate=rate)
        for maturity, rate in BILL_QUOTES
    ]


def _bonds() -> list[BondQuote]:
    return [
        BondQuote(
            settlement=SETTLEMENT, maturity=maturity, coupon_rate=coupon, frequency=2,
            price=price,
        )
        for maturity, coupon, price in BOND_QUOTES
    ]


def _raw_inputs() -> dict[str, object]:
    """The canonical raw-input payload for the build digest — values only."""
    return {
        "as_of": SETTLEMENT.isoformat(),
        "bills": [
            {"maturity": maturity.isoformat(), "discount_rate": rate}
            for maturity, rate in BILL_QUOTES
        ],
        "bonds": [
            {"maturity": maturity.isoformat(), "coupon": coupon, "clean_price": price}
            for maturity, coupon, price in BOND_QUOTES
        ],
        "mpr": MPR,
        "meeting_dates": [day.isoformat() for day in MPC_DATES_2026],
        "overnight_spread_bps": OVERNIGHT_SPREAD_BPS,
    }


def _build_pipeline() -> tuple[ZeroCurve, ForwardQaResult, NssFit, CurveBuildResult]:
    curve = bootstrap_zero_curve(_bills(), _bonds(), interpolation="monotone_convex")
    qa = qa_forwards(curve, QA_GRID)
    fallback_grid = sorted({*curve.times, 1.5, 3.0, 5.5, 8.5})
    fallback = fit_nss(fallback_grid, [curve.zero(t) for t in fallback_grid])
    definition = CurveDefinition(
        curve_code="AEQ.GHS.SOV.ZERO",
        curve_kind="zero",
        interpolation="monotone_convex",
        day_count=DayCount.ACT_364,
        extrapolation="flat_forward",
        instrument_selection=("duplicate_policy",),
    )
    result = CurveBuildResult.create(
        definition,
        CurveNodes(tenor_years=curve.times, values=curve.zeros),
        qa,
        _raw_inputs(),
    )
    return curve, qa, fallback, result


class TestBootstrapGhana:
    def test_all_seven_instruments_reprice(self) -> None:
        curve, _, _, _ = _build_pipeline()
        for bill in _bills():
            t = curve.time_of(bill.maturity)
            assert curve.df(t) == pytest.approx(bill.discount_factor(), abs=1e-10)
        for bond in _bonds():
            flows = bond_cashflows(
                bond.settlement, bond.maturity, bond.coupon_rate, bond.frequency,
                bond.day_count,
            )
            model = sum(
                flow.amount * curve.df(curve.time_of(flow.payment_date)) for flow in flows
            )
            assert model == pytest.approx(bond.dirty_price(), abs=1e-8)

    def test_short_end_matches_published_bog_yields(self) -> None:
        """The curve's bill nodes are exactly the BoG published quotes:
        DF = 1 - d*t, and the simple yield they imply is the published
        interest rate (the golden pairs, this time read off the curve)."""
        curve, _, _, _ = _build_pipeline()
        for (maturity, discount_rate), published_interest in zip(
            BILL_QUOTES, (5.7618, 7.6409, 12.9821), strict=True
        ):
            days = (maturity - SETTLEMENT).days
            t = days / 364.0
            assert curve.df(t) == pytest.approx(1.0 - discount_rate * t, abs=1e-12)
            simple_yield = (1.0 / curve.df(t) - 1.0) / t
            assert round(simple_yield * 100.0, 4) == pytest.approx(
                published_interest, abs=5e-5
            )
            assert simple_yield == pytest.approx(
                discount_to_yield(discount_rate, days), abs=1e-12
            )

    def test_curve_shape_is_sane(self) -> None:
        curve, _, _, _ = _build_pipeline()
        assert len(curve.times) == 7
        # Steep upward short end into a post-DDEP long end in the 12-15% band.
        assert 0.055 < curve.zero(0.25) < 0.060
        assert 0.115 < curve.zero(1.0) < 0.130
        for t in (2.0, 5.0, 7.0, 10.0):
            assert 0.12 < curve.zero(t) < 0.15


class TestQaGate:
    def test_forwards_pass_the_hard_gate(self) -> None:
        _, qa, _, _ = _build_pipeline()
        assert qa.passed
        assert qa.positivity_pass
        assert qa.min_forward > 0.0
        assert qa.oscillation_pass
        assert qa.total_variation_ratio < 3.0


class TestNssFallback:
    def test_fallback_tracks_the_bootstrap_curve(self) -> None:
        """NSS is the sparse-day fallback, not the primary: on this violently
        steep short end (5.7% -> 12.2% inside a year) it tracks within 100 bps
        everywhere and within 50 bps beyond 2y — the spec's documented
        trade-off (parametric robustness, lower point accuracy)."""
        curve, _, fallback, _ = _build_pipeline()
        assert fallback.max_abs_residual < 0.0100
        for t in (2.0, 3.0, 5.0, 7.0, 10.0):
            nss_value = nss_zero(t, *fallback.parameters.as_tuple())
            assert abs(float(nss_value) - curve.zero(t)) < 0.0050


class TestMeetingStepShortEnd:
    def test_discounting_short_end_from_policy_fixture(self) -> None:
        step = MeetingDateStepCurve(
            anchor_date=SETTLEMENT,
            mpr=MPR,
            meeting_dates=MPC_DATES_2026,
            spread_bps=OVERNIGHT_SPREAD_BPS,
            expected_moves_bps=(0.0, 0.0, 0.0, 0.0, -50.0, -50.0),
        )
        # Pre-anchor meetings never step; the Sep/Nov cuts do.
        assert step.overnight_rate(SETTLEMENT) == pytest.approx(0.0630)
        assert step.overnight_rate(date(2026, 10, 1)) == pytest.approx(0.0580)
        assert step.overnight_rate(date(2026, 12, 1)) == pytest.approx(0.0530)
        # DF to year-end by hand: 56 nights at 6.30%, 56 at 5.80%, 38 at 5.30%.
        growth = (
            (1.0 + 0.0630 / 365.0) ** 56
            * (1.0 + 0.0580 / 365.0) ** 56
            * (1.0 + 0.0530 / 365.0) ** 38
        )
        assert step.df(date(2026, 12, 31)) == pytest.approx(1.0 / growth, abs=1e-14)


class TestDeterminism:
    def test_two_runs_produce_identical_build_results(self) -> None:
        """The reproducibility invariant: same inputs, same everything —
        node values, QA verdict, NSS fit and the sha256 input digest."""
        curve_a, qa_a, nss_a, result_a = _build_pipeline()
        curve_b, qa_b, nss_b, result_b = _build_pipeline()
        assert curve_a == curve_b
        assert qa_a == qa_b
        assert nss_a == nss_b
        assert result_a == result_b
        assert result_a.input_digest == result_b.input_digest
        assert len(result_a.input_digest) == 64

    def test_a_one_tick_quote_change_changes_the_digest(self) -> None:
        _, _, _, baseline = _build_pipeline()
        tweaked_inputs = _raw_inputs()
        bills = tweaked_inputs["bills"]
        assert isinstance(bills, list)
        bills[0] = {"maturity": "2026-11-02", "discount_rate": 0.056801}
        tweaked = CurveBuildResult.create(
            baseline.definition, baseline.nodes, baseline.qa, tweaked_inputs
        )
        assert tweaked.input_digest != baseline.input_digest


class TestNumericalSanity:
    def test_discount_factors_strictly_decreasing(self) -> None:
        curve, _, _, _ = _build_pipeline()
        grid = [0.05 * i for i in range(1, 220)]
        dfs = [curve.df(t) for t in grid]
        assert all(b < a for a, b in zip(dfs, dfs[1:], strict=False))
        assert all(0.0 < df <= 1.0 for df in dfs)

    def test_forward_integrates_back_to_zero(self) -> None:
        """z(T)*T = integral of instantaneous forward — trapezoid quadrature
        over a fine grid reproduces the 10y zero to ~1e-6."""
        curve, _, _, _ = _build_pipeline()
        horizon = curve.times[-1]
        n = 20000
        step = horizon / n
        total = 0.0
        previous = curve.instantaneous_forward(1e-12)
        for i in range(1, n + 1):
            current = curve.instantaneous_forward(i * step)
            total += 0.5 * (previous + current) * step
            previous = current
        assert total / horizon == pytest.approx(curve.zero(horizon), abs=2e-6)
        assert math.isfinite(total)
