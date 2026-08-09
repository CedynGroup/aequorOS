"""The forward QA gate: positivity and oscillation scoring on known-good and
known-bad curves, built directly from hand-picked nodes so the failures are
constructed, not accidental."""

from __future__ import annotations

from datetime import date

import pytest

from app.domain.curves.bootstrap import ZeroCurve
from app.domain.curves.conventions import DayCount
from app.domain.curves.forwards import QaError, forward_curve, qa_forwards

VALUATION = date(2026, 8, 3)


def _curve(times: tuple[float, ...], zeros: tuple[float, ...], method: str) -> ZeroCurve:
    return ZeroCurve(
        valuation_date=VALUATION,
        day_count=DayCount.ACT_364,
        times=times,
        zeros=zeros,
        interpolation=method,
    )


SMOOTH = _curve((0.25, 1.0, 2.0, 5.0, 10.0), (0.06, 0.08, 0.10, 0.12, 0.13), "monotone_convex")
GRID = tuple(0.1 * i for i in range(1, 101))


class TestForwardCurve:
    def test_period_forwards_match_zero_identity(self) -> None:
        result = forward_curve(SMOOTH, (1.0, 2.0, 3.0))
        expected = (SMOOTH.zero(2.0) * 2.0 - SMOOTH.zero(1.0) * 1.0) / 1.0
        assert result.period_forwards[0] == pytest.approx(expected, abs=1e-15)
        assert len(result.period_forwards) == len(result.times) - 1

    def test_instantaneous_matches_curve(self) -> None:
        result = forward_curve(SMOOTH, (0.5, 1.5, 4.0))
        for t, value in zip(result.times, result.instantaneous, strict=True):
            assert value == SMOOTH.instantaneous_forward(t)

    def test_grid_validation(self) -> None:
        with pytest.raises(QaError):
            forward_curve(SMOOTH, (1.0, 2.0))  # too short
        with pytest.raises(QaError):
            forward_curve(SMOOTH, (1.0, 1.0, 2.0))  # not increasing
        with pytest.raises(QaError):
            forward_curve(SMOOTH, (-1.0, 1.0, 2.0))  # negative


class TestQaGate:
    def test_smooth_monotone_convex_curve_passes(self) -> None:
        result = qa_forwards(SMOOTH, GRID)
        assert result.passed
        assert result.positivity_pass
        assert result.oscillation_pass
        assert result.min_forward > 0.0
        assert result.total_variation_ratio <= 3.0

    def test_inverted_zeros_fail_positivity(self) -> None:
        # 10% at 1y falling to 2% at 2y: the 1y1y forward is
        # (0.02*2 - 0.10*1) / 1 = -0.06 — negative by construction.
        bad = _curve((1.0, 2.0, 3.0), (0.10, 0.02, 0.03), "linear_zero")
        result = qa_forwards(bad, (0.5, 1.0, 1.5, 2.0, 2.5, 3.0))
        assert not result.positivity_pass
        assert result.min_forward < 0.0
        assert not result.passed

    def test_positivity_check_can_be_waived(self) -> None:
        bad = _curve((1.0, 2.0, 3.0), (0.10, 0.02, 0.03), "linear_zero")
        result = qa_forwards(bad, (0.5, 1.0, 1.5, 2.0, 2.5, 3.0), positivity=False)
        assert result.positivity_pass  # not required
        assert not result.positivity_required

    def test_sawtooth_zeros_fail_oscillation(self) -> None:
        # Alternating zeros on linear interpolation produce ringing forwards:
        # every extra up-down cycle adds total variation while the range stays
        # bounded, so the ratio climbs past the gate.
        saw = _curve(
            (1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0),
            (0.10, 0.14, 0.09, 0.15, 0.08, 0.16, 0.07, 0.15),
            "linear_zero",
        )
        result = qa_forwards(saw, GRID[:80], positivity=False)
        assert result.slope_sign_changes >= 5
        assert result.total_variation_ratio > 3.0
        assert not result.oscillation_pass
        assert not result.passed

    def test_flat_curve_scores_ratio_one(self) -> None:
        flat = _curve((1.0, 2.0, 5.0), (0.08, 0.08, 0.08), "log_linear_df")
        result = qa_forwards(flat, (0.5, 1.5, 2.5, 3.5, 4.5))
        assert result.total_variation_ratio == 1.0
        assert result.slope_sign_changes == 0
        assert result.passed

    def test_monotone_forward_path_scores_ratio_one(self) -> None:
        # log-linear DF gives stepwise-increasing forwards for these nodes:
        # total variation equals the range exactly.
        steps = _curve((1.0, 2.0, 3.0), (0.05, 0.06, 0.07), "log_linear_df")
        result = qa_forwards(steps, (0.5, 1.5, 2.5))
        assert result.total_variation_ratio == pytest.approx(1.0, abs=1e-12)

    def test_tolerance_below_one_rejected(self) -> None:
        with pytest.raises(QaError):
            qa_forwards(SMOOTH, GRID, oscillation_tolerance=0.5)
