"""Engle-Granger + self-contained ADF on constructed series.

Seeded randomness is allowed HERE only (fixed ``numpy.random.default_rng(42)``)
— library code contains no randomness. The cointegrated pair is
``y = 0.3 + 0.95 x + AR(1) noise`` on a driftless random walk ``x``; the
non-cointegrated control is two independent walks from the same generator.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.domain.curves.cointegration import (
    ADF_CRITICAL_VALUES,
    CointegrationError,
    engle_granger,
    synthetic_ois_level,
)

N = 600
TRUE_ALPHA = 0.3
TRUE_BETA = 0.95


def _series() -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    rng = np.random.default_rng(42)
    x = np.cumsum(rng.normal(0.0, 0.5, N))
    noise = np.empty(N)
    noise[0] = 0.0
    shocks = rng.normal(0.0, 0.2, N)
    for i in range(1, N):
        noise[i] = 0.6 * noise[i - 1] + shocks[i]
    y = TRUE_ALPHA + TRUE_BETA * x + noise
    walk_a = np.cumsum(rng.normal(0.0, 0.5, N))
    walk_b = np.cumsum(rng.normal(0.0, 0.5, N))
    return y, x, walk_a, walk_b


class TestCointegratedPair:
    def test_beta_recovered_within_002(self) -> None:
        y, x, _, _ = _series()
        result = engle_granger(y, x)
        assert result.beta == pytest.approx(TRUE_BETA, abs=0.02)
        assert result.alpha == pytest.approx(TRUE_ALPHA, abs=0.15)

    def test_cointegration_detected_even_at_1pct(self) -> None:
        y, x, _, _ = _series()
        result = engle_granger(y, x)
        assert result.adf_stat < ADF_CRITICAL_VALUES["1%"]
        assert result.is_cointegrated("1%")
        assert result.is_cointegrated("5%")
        assert result.is_cointegrated("10%")

    def test_residuals_are_the_fitted_spread(self) -> None:
        y, x, _, _ = _series()
        result = engle_granger(y, x)
        assert len(result.residuals) == N
        reconstructed = y - result.alpha - result.beta * x
        assert np.allclose(np.asarray(result.residuals), reconstructed, atol=1e-12)
        # OLS residuals are mean-zero by construction (constant included)
        assert float(np.mean(np.asarray(result.residuals))) == pytest.approx(0.0, abs=1e-12)

    def test_deterministic(self) -> None:
        y, x, _, _ = _series()
        assert engle_granger(y, x) == engle_granger(y, x)


class TestIndependentWalks:
    def test_no_cointegration_detected(self) -> None:
        _, _, walk_a, walk_b = _series()
        result = engle_granger(walk_a, walk_b)
        assert result.adf_stat > ADF_CRITICAL_VALUES["10%"]
        assert not result.is_cointegrated("5%")
        assert not result.is_cointegrated("10%")


class TestAdfMechanics:
    def test_fixed_lag_selection(self) -> None:
        y, x, _, _ = _series()
        result = engle_granger(y, x, maxlag=4, lag_selection="fixed")
        assert result.adf_lags == 4
        assert result.is_cointegrated("5%")

    def test_aic_selects_within_bounds(self) -> None:
        y, x, _, _ = _series()
        result = engle_granger(y, x, maxlag=8, lag_selection="aic")
        assert 0 <= result.adf_lags <= 8
        # AR(1) noise: differencing leaves ~1 lag of structure to soak up.
        assert result.adf_lags <= 3

    def test_common_sample_across_lag_candidates(self) -> None:
        # The AIC comparison holds out maxlag observations, so the reported
        # sample size is invariant to the chosen lag.
        y, x, _, _ = _series()
        result = engle_granger(y, x, maxlag=8, lag_selection="aic")
        assert result.adf_nobs == N - 1 - 8

    def test_critical_values_are_mackinnon_2010_n2(self) -> None:
        assert ADF_CRITICAL_VALUES["1%"] == -3.90
        assert ADF_CRITICAL_VALUES["5%"] == -3.34
        assert ADF_CRITICAL_VALUES["10%"] == -3.04

    def test_unknown_significance_level_raises(self) -> None:
        y, x, _, _ = _series()
        result = engle_granger(y, x)
        with pytest.raises(CointegrationError):
            result.is_cointegrated("2.5%")


class TestValidation:
    def test_mismatched_series_raise(self) -> None:
        with pytest.raises(CointegrationError):
            engle_granger([1.0, 2.0], [1.0, 2.0, 3.0])

    def test_too_short_raises(self) -> None:
        with pytest.raises(CointegrationError, match="observations"):
            engle_granger(list(range(10)), list(range(10)))

    def test_non_finite_raises(self) -> None:
        y, x, _, _ = _series()
        y_bad = y.copy()
        y_bad[10] = np.nan
        with pytest.raises(CointegrationError, match="finite"):
            engle_granger(y_bad, x)

    def test_maxlag_out_of_range_raises(self) -> None:
        y, x, _, _ = _series()
        with pytest.raises(CointegrationError, match="maxlag"):
            engle_granger(y, x, maxlag=9)


class TestSyntheticLevel:
    def test_level_is_alpha_plus_beta_times_benchmark(self) -> None:
        assert synthetic_ois_level(0.25, alpha=0.01, beta=0.9) == pytest.approx(0.235)

    def test_round_trips_the_fitted_relationship(self) -> None:
        y, x, _, _ = _series()
        result = engle_granger(y, x)
        implied = synthetic_ois_level(float(x[-1]), result.alpha, result.beta)
        # The implied level differs from the observed y only by the residual.
        assert implied == pytest.approx(float(y[-1]) - result.residuals[-1], abs=1e-12)
