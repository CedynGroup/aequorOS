"""Property and golden tests for the four zero-curve interpolators.

The monotone convex checks follow the guarantees Hagan-West prove for the
method: node reproduction, forward continuity, positivity under
positive-discrete-forward inputs, and locality. The integral identity
(numeric quadrature of the instantaneous forward over an interval equals the
discrete forward mass ``r_i t_i - r_{i-1} t_{i-1}``) is the non-circular proof
that the four-region G function is the true integral of g.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.domain.curves.interpolation import (
    InterpolationError,
    LinearZero,
    LogLinearDF,
    MonotoneConvex,
    PchipZero,
    make_interpolator,
)

TIMES = (1.0, 2.0, 3.0, 4.0, 5.0, 6.0)
ZEROS = (0.03, 0.04, 0.047, 0.06, 0.06, 0.056)
ALL_CLASSES = (LinearZero, LogLinearDF, MonotoneConvex, PchipZero)


@pytest.mark.parametrize("cls", ALL_CLASSES)
class TestSharedProperties:
    def test_reproduces_input_zeros_at_nodes(self, cls: type) -> None:
        interpolator = cls(TIMES, ZEROS)
        for t, z in zip(TIMES, ZEROS, strict=True):
            assert interpolator.zero(t) == pytest.approx(z, abs=1e-12)

    def test_call_is_zero(self, cls: type) -> None:
        interpolator = cls(TIMES, ZEROS)
        assert interpolator(2.5) == interpolator.zero(2.5)

    def test_flat_zero_extrapolation(self, cls: type) -> None:
        interpolator = cls(TIMES, ZEROS, extrapolation="flat_zero")
        assert interpolator.zero(25.0) == pytest.approx(ZEROS[-1], abs=1e-15)
        assert interpolator.instantaneous_forward(25.0) == pytest.approx(ZEROS[-1], abs=1e-15)

    def test_flat_forward_extrapolation_holds_terminal_forward(self, cls: type) -> None:
        interpolator = cls(TIMES, ZEROS, extrapolation="flat_forward")
        terminal = interpolator.instantaneous_forward(50.0)
        assert interpolator.instantaneous_forward(100.0) == pytest.approx(terminal, abs=1e-15)
        # zeros asymptote toward the held forward: z(t) = (z_n t_n + f (t - t_n))/t,
        # so at t = 1e6 the node contribution is O(1e-6).
        assert interpolator.zero(1.0e6) == pytest.approx(terminal, abs=1e-5)

    def test_forward_zero_identity(self, cls: type) -> None:
        """f(t) = d/dt [t z(t)] — checked by central finite difference."""
        interpolator = cls(TIMES, ZEROS)
        step = 1e-6
        for t in (1.5, 2.5, 3.7, 4.5, 5.5):
            numeric = (
                (t + step) * interpolator.zero(t + step)
                - (t - step) * interpolator.zero(t - step)
            ) / (2.0 * step)
            assert interpolator.instantaneous_forward(t) == pytest.approx(numeric, abs=1e-6)

    def test_derivative_matches_finite_difference(self, cls: type) -> None:
        interpolator = cls(TIMES, ZEROS)
        step = 1e-7
        for t in (1.5, 3.3, 5.5):
            numeric = (interpolator.zero(t + step) - interpolator.zero(t - step)) / (2.0 * step)
            assert interpolator.derivative(t) == pytest.approx(numeric, abs=1e-5)

    def test_negative_time_rejected(self, cls: type) -> None:
        interpolator = cls(TIMES, ZEROS)
        with pytest.raises(InterpolationError):
            interpolator.zero(-0.1)

    def test_bad_nodes_rejected(self, cls: type) -> None:
        with pytest.raises(InterpolationError):
            cls((1.0, 1.0, 2.0), (0.03, 0.04, 0.05))
        with pytest.raises(InterpolationError):
            cls((0.0, 1.0), (0.03, 0.04))
        with pytest.raises(InterpolationError):
            cls((1.0, 2.0), (0.03,))


class TestLinearZero:
    def test_midpoint_is_arithmetic_mean(self) -> None:
        interpolator = LinearZero(TIMES, ZEROS)
        assert interpolator.zero(1.5) == pytest.approx(0.035, abs=1e-15)

    def test_derivative_is_segment_slope(self) -> None:
        interpolator = LinearZero(TIMES, ZEROS)
        assert interpolator.derivative(2.5) == pytest.approx(0.007, abs=1e-15)
        assert interpolator.derivative(0.5) == 0.0  # flat below the first node

    def test_forward_is_sawtooth_discontinuous(self) -> None:
        interpolator = LinearZero(TIMES, ZEROS)
        left = interpolator.instantaneous_forward(2.0 - 1e-9)
        right = interpolator.instantaneous_forward(2.0 + 1e-9)
        assert abs(left - right) > 1e-4  # the documented cost of linear-on-zeros


class TestLogLinearDF:
    def test_forwards_piecewise_constant(self) -> None:
        interpolator = LogLinearDF(TIMES, ZEROS)
        inside_a = interpolator.instantaneous_forward(2.2)
        inside_b = interpolator.instantaneous_forward(2.8)
        assert inside_a == pytest.approx(inside_b, abs=1e-15)
        # and equal to the discrete forward of the segment
        expected = (ZEROS[2] * TIMES[2] - ZEROS[1] * TIMES[1]) / (TIMES[2] - TIMES[1])
        assert inside_a == pytest.approx(expected, abs=1e-15)

    def test_positive_forwards_when_dfs_decrease(self) -> None:
        interpolator = LogLinearDF(TIMES, ZEROS)
        for t in np.linspace(0.05, 6.0, 200):
            assert interpolator.instantaneous_forward(float(t)) > 0.0

    def test_short_end_constant_forward_through_origin(self) -> None:
        interpolator = LogLinearDF(TIMES, ZEROS)
        assert interpolator.zero(0.5) == pytest.approx(ZEROS[0], abs=1e-15)
        assert interpolator.instantaneous_forward(0.5) == pytest.approx(ZEROS[0], abs=1e-15)


class TestMonotoneConvex:
    def test_forwards_continuous_at_nodes(self) -> None:
        interpolator = MonotoneConvex(TIMES, ZEROS)
        for t in TIMES[:-1]:
            left = interpolator.instantaneous_forward(t - 1e-9)
            right = interpolator.instantaneous_forward(t + 1e-9)
            assert left == pytest.approx(right, abs=1e-7)

    def test_forwards_positive_for_positive_discrete_forwards(self) -> None:
        # Steeply inverted zeros whose r*t products still increase (all
        # discrete forwards positive) — the modifier must keep f(t) >= 0.
        times = (1.0, 2.0, 3.0, 4.0)
        zeros = (0.10, 0.062, 0.045, 0.036)
        interpolator = MonotoneConvex(times, zeros)
        for t in np.linspace(0.01, 4.0, 400):
            assert interpolator.instantaneous_forward(float(t)) >= 0.0

    def test_integral_identity_proves_g_function(self) -> None:
        """Quadrature of f over [t_{i-1}, t_i] equals r_i t_i - r_{i-1} t_{i-1}."""
        interpolator = MonotoneConvex(TIMES, ZEROS)
        knots = (0.0, *TIMES)
        products = (0.0, *(z * t for z, t in zip(ZEROS, TIMES, strict=True)))
        for i in range(1, len(knots)):
            grid = np.linspace(knots[i - 1] + 1e-12, knots[i], 4001)
            values = np.array([interpolator.instantaneous_forward(float(t)) for t in grid])
            integral = float(np.trapezoid(values, grid))
            assert integral == pytest.approx(products[i] - products[i - 1], abs=1e-8)

    def test_locality(self) -> None:
        """Perturbing node 4 (t=4.0) must not move sections >= 2 intervals away."""
        base = MonotoneConvex(TIMES, ZEROS)
        perturbed_zeros = list(ZEROS)
        perturbed_zeros[3] += 0.005
        perturbed = MonotoneConvex(TIMES, tuple(perturbed_zeros))
        for t in (0.5, 1.5):  # intervals [0,1] and [1,2]: untouched
            assert perturbed.zero(t) == base.zero(t)
            assert perturbed.instantaneous_forward(t) == base.instantaneous_forward(t)
        assert abs(perturbed.zero(3.5) - base.zero(3.5)) > 1e-6  # neighbour moves

    def test_input_monotonicity_preserved(self) -> None:
        # Monotone increasing zeros in, monotone increasing zeros out.
        times = (1.0, 2.0, 3.0, 5.0, 10.0)
        zeros = (0.05, 0.07, 0.08, 0.10, 0.12)
        interpolator = MonotoneConvex(times, zeros)
        samples = [interpolator.zero(float(t)) for t in np.linspace(1.0, 10.0, 500)]
        assert all(b >= a - 1e-12 for a, b in zip(samples, samples[1:], strict=False))

    def test_single_node_flat(self) -> None:
        interpolator = MonotoneConvex((1.0,), (0.08,))
        assert interpolator.zero(0.5) == pytest.approx(0.08, abs=1e-15)
        assert interpolator.zero(1.0) == pytest.approx(0.08, abs=1e-15)
        assert interpolator.instantaneous_forward(0.7) == pytest.approx(0.08, abs=1e-15)

    def test_zero_at_origin_is_boundary_forward(self) -> None:
        interpolator = MonotoneConvex(TIMES, ZEROS)
        assert interpolator.zero(0.0) == interpolator.instantaneous_forward(0.0)


class TestPchip:
    def test_zero_curve_is_c1(self) -> None:
        """PCHIP zeros have continuous first derivative at nodes."""
        interpolator = PchipZero(TIMES, ZEROS)
        for t in TIMES[1:-1]:
            left = interpolator.derivative(t - 1e-8)
            right = interpolator.derivative(t + 1e-8)
            assert left == pytest.approx(right, abs=1e-5)

    def test_no_overshoot_on_monotone_data(self) -> None:
        times = (1.0, 2.0, 3.0, 5.0, 10.0)
        zeros = (0.05, 0.07, 0.08, 0.10, 0.12)
        interpolator = PchipZero(times, zeros)
        for t in np.linspace(1.0, 10.0, 500):
            value = interpolator.zero(float(t))
            assert 0.05 - 1e-12 <= value <= 0.12 + 1e-12

    def test_requires_two_nodes(self) -> None:
        with pytest.raises(InterpolationError):
            PchipZero((1.0,), (0.08,))


class TestFactory:
    @pytest.mark.parametrize(
        ("method", "cls"),
        [
            ("linear_zero", LinearZero),
            ("log_linear_df", LogLinearDF),
            ("monotone_convex", MonotoneConvex),
            ("pchip", PchipZero),
        ],
    )
    def test_maps_names(self, method: str, cls: type) -> None:
        assert isinstance(make_interpolator(method, TIMES, ZEROS), cls)

    def test_unknown_method_raises(self) -> None:
        with pytest.raises(InterpolationError):
            make_interpolator("cubic_spline", TIMES, ZEROS)

    def test_unknown_extrapolation_raises(self) -> None:
        with pytest.raises(InterpolationError):
            LinearZero(TIMES, ZEROS, extrapolation="linear")  # type: ignore[arg-type]
