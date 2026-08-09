"""Nelson-Siegel-Svensson: limits, curve recovery and determinism.

Parameter recovery is deliberately NOT asserted — NSS parameters are known to
be degenerate (tau swaps, beta trade-offs). What must hold is that the fitted
CURVE reproduces the generating curve, and that the fit is a pure function of
its inputs (bitwise-identical across runs).
"""

from __future__ import annotations

import numpy as np
import pytest

from app.domain.curves.nss import NssBounds, NssParameters, fit_nss, nss_zero

TRUE = NssParameters(beta0=0.12, beta1=-0.06, beta2=0.08, beta3=-0.03, tau1=1.2, tau2=6.0)
GRID = np.array([0.25, 0.5, 1.0, 2.0, 3.0, 5.0, 7.0, 10.0, 15.0, 20.0])


class TestNssZero:
    def test_t_zero_limit_is_beta0_plus_beta1(self) -> None:
        assert nss_zero(0.0, *TRUE.as_tuple()) == pytest.approx(0.06, abs=1e-15)

    def test_t_infinity_limit_is_beta0(self) -> None:
        # The slope/curvature loadings decay like tau/t, so at t = 1e6 the
        # non-level terms are O(1e-7).
        assert nss_zero(1e6, *TRUE.as_tuple()) == pytest.approx(TRUE.beta0, abs=1e-6)

    def test_small_t_continuous_at_switch(self) -> None:
        # The series-expansion branch and the exact branch agree at the seam.
        just_below = float(np.asarray(nss_zero(1e-11, *TRUE.as_tuple())))
        just_above = float(np.asarray(nss_zero(1e-9, *TRUE.as_tuple())))
        assert just_below == pytest.approx(just_above, abs=1e-9)

    def test_vectorized_matches_scalar(self) -> None:
        vector = np.asarray(nss_zero(GRID, *TRUE.as_tuple()))
        scalars = [float(np.asarray(nss_zero(float(t), *TRUE.as_tuple()))) for t in GRID]
        assert vector == pytest.approx(scalars, abs=1e-15)

    def test_hand_computed_point(self) -> None:
        # t = tau1 = 1.2, x = 1: slope loading (1-e^-1), curvature (1-e^-1)-e^-1.
        t = 1.2
        x2 = t / 6.0
        expected = (
            0.12
            + -0.06 * (1 - np.exp(-1.0))
            + 0.08 * ((1 - np.exp(-1.0)) - np.exp(-1.0))
            + -0.03 * ((1 - np.exp(-x2)) / x2 - np.exp(-x2))
        )
        assert nss_zero(t, *TRUE.as_tuple()) == pytest.approx(float(expected), abs=1e-15)

    def test_invalid_inputs_raise(self) -> None:
        with pytest.raises(ValueError, match="taus"):
            nss_zero(1.0, 0.1, 0.0, 0.0, 0.0, -1.0, 5.0)
        with pytest.raises(ValueError, match="non-negative"):
            nss_zero(-1.0, *TRUE.as_tuple())


class TestFitNss:
    def test_curve_recovery_within_1e6(self) -> None:
        """Zeros generated from known params over 0.25y-20y refit to the same
        CURVE within 1e-6 (parameters themselves may differ — degeneracy)."""
        zeros = np.asarray(nss_zero(GRID, *TRUE.as_tuple()))
        fit = fit_nss(GRID, zeros)
        refit = np.asarray(nss_zero(GRID, *fit.parameters.as_tuple()))
        assert float(np.max(np.abs(refit - zeros))) < 1e-6
        # and off-grid too — same curve, not just the fitted points
        dense = np.linspace(0.25, 20.0, 200)
        truth = np.asarray(nss_zero(dense, *TRUE.as_tuple()))
        fitted = np.asarray(nss_zero(dense, *fit.parameters.as_tuple()))
        assert float(np.max(np.abs(fitted - truth))) < 1e-5

    def test_fit_is_deterministic(self) -> None:
        zeros = np.asarray(nss_zero(GRID, *TRUE.as_tuple()))
        first = fit_nss(GRID, zeros)
        second = fit_nss(GRID, zeros)
        assert first == second  # bitwise-equal dataclasses, digest-grade determinism

    def test_explicit_start_overrides_grid(self) -> None:
        zeros = np.asarray(nss_zero(GRID, *TRUE.as_tuple()))
        fit = fit_nss(GRID, zeros, x0=TRUE)
        assert fit.start_index == 0
        assert fit.max_abs_residual < 1e-8

    def test_bounds_are_respected(self) -> None:
        zeros = np.asarray(nss_zero(GRID, *TRUE.as_tuple()))
        bounds = NssBounds(
            lower=(0.0, -0.5, -0.5, -0.5, 0.5, 0.5),
            upper=(0.5, 0.5, 0.5, 0.5, 20.0, 20.0),
        )
        fit = fit_nss(GRID, zeros, bounds=bounds)
        for value, low, high in zip(
            fit.parameters.as_tuple(), bounds.lower, bounds.upper, strict=True
        ):
            assert low - 1e-12 <= value <= high + 1e-12

    def test_too_few_points_raise(self) -> None:
        with pytest.raises(ValueError, match="six"):
            fit_nss([1.0, 2.0, 3.0], [0.1, 0.1, 0.1])

    def test_mismatched_inputs_raise(self) -> None:
        with pytest.raises(ValueError, match="equally long"):
            fit_nss([1.0, 2.0], [0.1, 0.1, 0.1])
