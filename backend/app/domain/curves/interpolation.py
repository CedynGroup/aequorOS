"""Zero-curve interpolators (spec §6.3 — interpolation choices).

Every interpolator works on ``(time, zero_rate)`` nodes where zero rates are
**continuously compounded** decimals — the natural space for forward
derivation because the instantaneous forward is exactly
``f(t) = d/dt [t * z(t)] = z(t) + t * z'(t)`` and ``df(t) = exp(-z(t) * t)``.

Four methods, per the spec's decision table:

- :class:`LinearZero` — linear on zero rates; simple, sawtooth forwards.
- :class:`LogLinearDF` — linear on log discount factors, i.e. piecewise-constant
  forwards; positivity-preserving; the OIS workhorse.
- :class:`MonotoneConvex` — the full Hagan-West (2006/2008) method: continuous
  positive forwards, locality, input monotonicity preserved. The flagship.
- :class:`PchipZero` — scipy's piecewise cubic Hermite on zeros (Lartey & Li
  2018 found PCH best for Ghana secondary-market zero curves).

Extrapolation is flat on both ends: below the first node the zero rate is held
flat (equivalently, constant forward from t=0 for log-linear DF), and beyond
the last node the rule is configurable — ``"flat_forward"`` (default: hold the
terminal instantaneous forward, the spec §5 step-5 long-end rule) or
``"flat_zero"`` (hold the terminal zero rate).
"""

from __future__ import annotations

import bisect
import math
from collections.abc import Sequence
from typing import Literal, Protocol

from scipy.interpolate import PchipInterpolator

__all__ = [
    "Extrapolation",
    "InterpolationError",
    "LinearZero",
    "LogLinearDF",
    "MonotoneConvex",
    "PchipZero",
    "ZeroInterpolator",
    "make_interpolator",
    "INTERPOLATION_METHODS",
]

type Extrapolation = Literal["flat_forward", "flat_zero"]


class InterpolationError(ValueError):
    """The node set or query violates the interpolator's contract."""


class ZeroInterpolator(Protocol):
    """Common protocol: callable zero rate plus derivative and forward."""

    def __call__(self, t: float) -> float: ...

    def zero(self, t: float) -> float: ...

    def derivative(self, t: float) -> float: ...

    def instantaneous_forward(self, t: float) -> float: ...


def _validate_nodes(times: Sequence[float], zeros: Sequence[float]) -> tuple[
    tuple[float, ...], tuple[float, ...]
]:
    if len(times) != len(zeros):
        raise InterpolationError("times and zeros must have the same length.")
    if not times:
        raise InterpolationError("At least one node is required.")
    cleaned_times = tuple(float(t) for t in times)
    cleaned_zeros = tuple(float(z) for z in zeros)
    if cleaned_times[0] <= 0.0:
        raise InterpolationError("Node times must be strictly positive (t=0 is the anchor).")
    for left, right in zip(cleaned_times, cleaned_times[1:], strict=False):
        if right <= left:
            raise InterpolationError("Node times must be strictly increasing.")
    for value in (*cleaned_times, *cleaned_zeros):
        if not math.isfinite(value):
            raise InterpolationError("Node times and zeros must be finite.")
    return cleaned_times, cleaned_zeros


class _BaseInterpolator:
    """Shared node storage, validation and flat extrapolation plumbing."""

    def __init__(
        self,
        times: Sequence[float],
        zeros: Sequence[float],
        extrapolation: Extrapolation = "flat_forward",
    ) -> None:
        self.times, self.zeros = _validate_nodes(times, zeros)
        if extrapolation not in ("flat_forward", "flat_zero"):
            raise InterpolationError(f"Unknown extrapolation rule '{extrapolation}'.")
        self.extrapolation: Extrapolation = extrapolation

    # -- subclass hooks -----------------------------------------------------

    def _zero_inside(self, t: float) -> float:
        """Zero rate for 0 < t <= last node time."""
        raise NotImplementedError

    def _forward_inside(self, t: float) -> float:
        """Instantaneous forward for 0 < t <= last node time."""
        raise NotImplementedError

    def _terminal_forward(self) -> float:
        """Instantaneous forward at the last node (left limit)."""
        raise NotImplementedError

    # -- public surface -----------------------------------------------------

    def __call__(self, t: float) -> float:
        return self.zero(t)

    def zero(self, t: float) -> float:
        self._check_time(t)
        last_time = self.times[-1]
        if t == 0.0:
            return self._forward_inside(0.0) if self._defined_at_origin() else self.zeros[0]
        if t <= last_time:
            return self._zero_inside(t)
        if self.extrapolation == "flat_zero":
            return self.zeros[-1]
        forward = self._terminal_forward()
        return (self.zeros[-1] * last_time + forward * (t - last_time)) / t

    def instantaneous_forward(self, t: float) -> float:
        self._check_time(t)
        last_time = self.times[-1]
        if t <= last_time:
            return self._forward_inside(t)
        if self.extrapolation == "flat_zero":
            return self.zeros[-1]
        return self._terminal_forward()

    def derivative(self, t: float) -> float:
        """d(zero)/dt via the identity f(t) = z(t) + t z'(t)  =>  z' = (f - z)/t.

        At t=0 the identity degenerates; a one-sided finite difference of the
        zero function stands in (documented boundary-only approximation).
        """
        self._check_time(t)
        if t < 1e-9:
            step = 1e-7
            return (self.zero(t + step) - self.zero(t)) / step
        return (self.instantaneous_forward(t) - self.zero(t)) / t

    # -- helpers ------------------------------------------------------------

    def _defined_at_origin(self) -> bool:
        """Whether the method natively defines values on (0, t_1)."""
        return False

    @staticmethod
    def _check_time(t: float) -> None:
        if not math.isfinite(t) or t < 0.0:
            raise InterpolationError(f"Query time must be finite and non-negative, got {t}.")

    def _segment(self, t: float) -> int:
        """Index i such that times[i] <= t < times[i+1] (clamped to valid range)."""
        index = bisect.bisect_right(self.times, t) - 1
        return max(0, min(index, len(self.times) - 2))


class LinearZero(_BaseInterpolator):
    """Linear interpolation on zero rates; flat below the first node.

    Forwards are the exact identity ``f = z + t z'`` — piecewise linear with
    sawtooth discontinuities at nodes (the spec calls this out as the cost of
    simplicity). ``derivative`` is the exact piecewise slope.
    """

    def _zero_inside(self, t: float) -> float:
        if t <= self.times[0] or len(self.times) == 1:
            return self.zeros[0]
        i = self._segment(t)
        t0, t1 = self.times[i], self.times[i + 1]
        z0, z1 = self.zeros[i], self.zeros[i + 1]
        weight = (t - t0) / (t1 - t0)
        return z0 + weight * (z1 - z0)

    def derivative(self, t: float) -> float:
        self._check_time(t)
        if t < self.times[0] or len(self.times) == 1:
            return 0.0
        if t > self.times[-1]:
            if self.extrapolation == "flat_zero":
                return 0.0
            # z(t) = (z_n t_n + f (t - t_n)) / t  =>  z'(t) = (f - z(t)) / t
            return (self._terminal_forward() - self.zero(t)) / t
        i = self._segment(t)
        return (self.zeros[i + 1] - self.zeros[i]) / (self.times[i + 1] - self.times[i])

    def _forward_inside(self, t: float) -> float:
        return self._zero_inside(t) + t * self.derivative(t)

    def _terminal_forward(self) -> float:
        if len(self.times) == 1:
            return self.zeros[0]
        i = len(self.times) - 2
        slope = (self.zeros[i + 1] - self.zeros[i]) / (self.times[i + 1] - self.times[i])
        return self.zeros[-1] + self.times[-1] * slope


class LogLinearDF(_BaseInterpolator):
    """Linear interpolation on log discount factors.

    ``L(t) = -z(t) * t`` is piecewise linear through ``(0, 0)`` and the nodes,
    so the instantaneous forward ``f = -dL/dt`` is piecewise constant and
    strictly positive whenever the discount factors strictly decrease —
    the positivity-preserving property the spec recommends for OIS curves.
    """

    def __init__(
        self,
        times: Sequence[float],
        zeros: Sequence[float],
        extrapolation: Extrapolation = "flat_forward",
    ) -> None:
        super().__init__(times, zeros, extrapolation)
        # log-DF knots, anchored at (0, 0).
        self._knot_times = (0.0, *self.times)
        self._knot_logdf = (0.0, *(-z * t for z, t in zip(self.zeros, self.times, strict=True)))

    def _defined_at_origin(self) -> bool:
        return True

    def _log_df(self, t: float) -> float:
        i = max(0, min(bisect.bisect_right(self._knot_times, t) - 1, len(self._knot_times) - 2))
        t0, t1 = self._knot_times[i], self._knot_times[i + 1]
        l0, l1 = self._knot_logdf[i], self._knot_logdf[i + 1]
        return l0 + (t - t0) / (t1 - t0) * (l1 - l0)

    def _zero_inside(self, t: float) -> float:
        return -self._log_df(t) / t

    def _forward_inside(self, t: float) -> float:
        # bisect + clamp: right-continuous at interior nodes, left segment at the
        # terminal node (the clamp lands the terminal query in the last segment).
        i = max(0, min(bisect.bisect_right(self._knot_times, t) - 1, len(self._knot_times) - 2))
        t0, t1 = self._knot_times[i], self._knot_times[i + 1]
        l0, l1 = self._knot_logdf[i], self._knot_logdf[i + 1]
        return -(l1 - l0) / (t1 - t0)

    def _terminal_forward(self) -> float:
        t0, t1 = self._knot_times[-2], self._knot_times[-1]
        l0, l1 = self._knot_logdf[-2], self._knot_logdf[-1]
        return -(l1 - l0) / (t1 - t0)


class PchipZero(_BaseInterpolator):
    """Piecewise cubic Hermite (PCHIP) on zero rates via scipy.

    Shape-preserving (no spline ringing); Lartey & Li (2018) found piecewise
    cubic Hermite best for the Ghana secondary-market zero curve. Forwards are
    continuous inside the node span (``f = z + t z'`` with C1 zeros).
    """

    def __init__(
        self,
        times: Sequence[float],
        zeros: Sequence[float],
        extrapolation: Extrapolation = "flat_forward",
    ) -> None:
        super().__init__(times, zeros, extrapolation)
        if len(self.times) < 2:
            raise InterpolationError("PCHIP requires at least two nodes.")
        self._spline = PchipInterpolator(self.times, self.zeros, extrapolate=False)
        self._spline_derivative = self._spline.derivative()

    def _zero_inside(self, t: float) -> float:
        if t <= self.times[0]:
            return self.zeros[0]
        return float(self._spline(t))

    def _slope_inside(self, t: float) -> float:
        if t <= self.times[0]:
            return 0.0
        return float(self._spline_derivative(t))

    def _forward_inside(self, t: float) -> float:
        return self._zero_inside(t) + t * self._slope_inside(t)

    def _terminal_forward(self) -> float:
        return self.zeros[-1] + self.times[-1] * float(self._spline_derivative(self.times[-1]))


class MonotoneConvex(_BaseInterpolator):
    """Hagan-West monotone convex interpolation (2006; Wilmott 2008).

    The method models the **instantaneous forward** directly. With node times
    ``t_1 < ... < t_n`` (and the anchor ``t_0 = 0``), it computes the discrete
    forwards ``fd_i = (r_i t_i - r_{i-1} t_{i-1}) / (t_i - t_{i-1})``, estimates
    node forwards ``f_i`` by the adjacent-interval weighted average (boundary
    values extrapolated so the end intervals average correctly), applies the
    positivity **modifier** ``f_i <- bound(0, f_i, 2 min(fd_i, fd_{i+1}))``
    (``f_0 <- bound(0, f_0, 2 fd_1)``, ``f_n <- bound(0, f_n, 2 fd_n)``), and on
    each interval builds ``f(t) = fd_i + g(x)`` where ``g`` is the paper's
    four-region function with ``g(0) = f_{i-1} - fd_i``, ``g(1) = f_i - fd_i``
    and ``integral_0^1 g = 0`` — so the curve reprices every node exactly.

    Guarantees (paper §6): forwards are continuous and positive whenever the
    discrete forwards are positive; the method is local (a node move only
    reshapes adjacent intervals); input monotonicity is preserved.
    """

    def __init__(
        self,
        times: Sequence[float],
        zeros: Sequence[float],
        extrapolation: Extrapolation = "flat_forward",
        enforce_positivity: bool = True,
    ) -> None:
        super().__init__(times, zeros, extrapolation)
        self.enforce_positivity = enforce_positivity
        # Interval boundaries including the t_0 = 0 anchor (r_0 t_0 = 0).
        knot_times = (0.0, *self.times)
        products = (0.0, *(z * t for z, t in zip(self.zeros, self.times, strict=True)))
        n = len(self.times)
        discrete = [
            (products[i] - products[i - 1]) / (knot_times[i] - knot_times[i - 1])
            for i in range(1, n + 1)
        ]
        node_forwards = [0.0] * (n + 1)
        for i in range(1, n):
            left_width = knot_times[i] - knot_times[i - 1]
            right_width = knot_times[i + 1] - knot_times[i]
            span = knot_times[i + 1] - knot_times[i - 1]
            node_forwards[i] = (left_width * discrete[i] + right_width * discrete[i - 1]) / span
        if n == 1:
            node_forwards[0] = discrete[0]
            node_forwards[n] = discrete[0]
        else:
            node_forwards[0] = discrete[0] - 0.5 * (node_forwards[1] - discrete[0])
            node_forwards[n] = discrete[-1] - 0.5 * (node_forwards[n - 1] - discrete[-1])
        if enforce_positivity:
            node_forwards[0] = _bound(0.0, node_forwards[0], 2.0 * discrete[0])
            node_forwards[n] = _bound(0.0, node_forwards[n], 2.0 * discrete[-1])
            for i in range(1, n):
                ceiling = 2.0 * min(discrete[i - 1], discrete[i])
                node_forwards[i] = _bound(0.0, node_forwards[i], ceiling)
        self._knot_times = knot_times
        self._products = products
        self._discrete = tuple(discrete)
        self._node_forwards = tuple(node_forwards)

    def _defined_at_origin(self) -> bool:
        return True  # the method natively covers [0, t_1] via the t_0 = 0 anchor

    def _locate(self, t: float) -> int:
        """Interval index i (1-based over knots) with knot[i-1] <= t <= knot[i]."""
        index = bisect.bisect_right(self._knot_times, t) - 1
        return max(1, min(index + 1, len(self._knot_times) - 1))

    def _zero_inside(self, t: float) -> float:
        if t == 0.0:
            return self._node_forwards[0]
        i = self._locate(t)
        t0, t1 = self._knot_times[i - 1], self._knot_times[i]
        width = t1 - t0
        x = (t - t0) / width
        g0 = self._node_forwards[i - 1] - self._discrete[i - 1]
        g1 = self._node_forwards[i] - self._discrete[i - 1]
        _, integral = _hagan_west_g(g0, g1, x)
        value = self._products[i - 1] + self._discrete[i - 1] * (t - t0) + width * integral
        return value / t

    def _forward_inside(self, t: float) -> float:
        if t == 0.0:
            return self._node_forwards[0]
        i = self._locate(t)
        t0, t1 = self._knot_times[i - 1], self._knot_times[i]
        x = (t - t0) / (t1 - t0)
        g0 = self._node_forwards[i - 1] - self._discrete[i - 1]
        g1 = self._node_forwards[i] - self._discrete[i - 1]
        g_value, _ = _hagan_west_g(g0, g1, x)
        return self._discrete[i - 1] + g_value

    def _terminal_forward(self) -> float:
        return self._node_forwards[-1]


def _bound(minimum: float, value: float, maximum: float) -> float:
    """West's bound(): clamp ``value`` into [minimum, maximum] (maximum wins)."""
    return min(max(minimum, value), maximum)


def _hagan_west_g(  # noqa: PLR0911, PLR0912 - four regions x two branches each, per the paper
    g0: float, g1: float, x: float
) -> tuple[float, float]:
    """The four-region g function and its running integral G(x) = int_0^x g.

    Regions per Hagan-West (Wilmott 2008); every branch satisfies g(0)=g0,
    g(1)=g1 and G(1)=0 (boundary-value discontinuities in degenerate
    zero-parameter cases are measure-zero and do not affect zeros).
    """
    if x <= 0.0:
        return g0, 0.0
    if x >= 1.0:
        return g1, 0.0
    if g0 == 0.0 and g1 == 0.0:
        return 0.0, 0.0
    if (g0 < 0.0 and -0.5 * g0 <= g1 <= -2.0 * g0) or (
        g0 > 0.0 and -0.5 * g0 >= g1 >= -2.0 * g0
    ):
        # Region (i): the plain quadratic is already monotone.
        g_value = g0 * (1.0 - 4.0 * x + 3.0 * x * x) + g1 * (-2.0 * x + 3.0 * x * x)
        integral = g0 * (x - 2.0 * x * x + x**3) + g1 * (-(x * x) + x**3)
        return g_value, integral
    if (g0 < 0.0 and g1 > -2.0 * g0) or (g0 > 0.0 and g1 < -2.0 * g0):
        # Region (ii): flat segment then a quadratic.
        eta = (g1 + 2.0 * g0) / (g1 - g0)
        if x <= eta:
            return g0, g0 * x
        ratio = (x - eta) / (1.0 - eta)
        g_value = g0 + (g1 - g0) * ratio * ratio
        integral = g0 * x + (g1 - g0) * (1.0 - eta) / 3.0 * ratio**3
        return g_value, integral
    if (g0 > 0.0 and 0.0 > g1 > -0.5 * g0) or (g0 < 0.0 and 0.0 < g1 < -0.5 * g0):
        # Region (iii): quadratic then a flat segment.
        eta = 3.0 * g1 / (g1 - g0)
        if x < eta:
            ratio = (eta - x) / eta
            g_value = g1 + (g0 - g1) * ratio * ratio
            integral = g1 * x + (g0 - g1) * eta / 3.0 * (1.0 - ratio**3)
            return g_value, integral
        return g1, g1 * x + (g0 - g1) * eta / 3.0
    # Region (iv): g0 and g1 share a sign (or one is zero).
    eta = g1 / (g1 + g0)
    coefficient = -g0 * g1 / (g0 + g1)
    if eta > 0.0 and x < eta:
        ratio = (eta - x) / eta
        g_value = coefficient + (g0 - coefficient) * ratio * ratio
        integral = coefficient * x + (g0 - coefficient) * eta / 3.0 * (1.0 - ratio**3)
        return g_value, integral
    if eta < 1.0:
        ratio = (x - eta) / (1.0 - eta)
        g_value = coefficient + (g1 - coefficient) * ratio * ratio
        integral = (
            coefficient * x
            + (g0 - coefficient) * eta / 3.0
            + (g1 - coefficient) * (1.0 - eta) / 3.0 * ratio**3
        )
        return g_value, integral
    # eta == 1 (g0 == 0): g collapses to the constant A == 0 on (0, 1).
    return coefficient, coefficient * x


INTERPOLATION_METHODS: tuple[str, ...] = (
    "linear_zero",
    "log_linear_df",
    "monotone_convex",
    "pchip",
)


def make_interpolator(
    method: str,
    times: Sequence[float],
    zeros: Sequence[float],
    extrapolation: Extrapolation = "flat_forward",
) -> ZeroInterpolator:
    """Factory keyed by the method names used in :class:`CurveDefinition`."""
    if method == "linear_zero":
        return LinearZero(times, zeros, extrapolation)
    if method == "log_linear_df":
        return LogLinearDF(times, zeros, extrapolation)
    if method == "monotone_convex":
        return MonotoneConvex(times, zeros, extrapolation)
    if method == "pchip":
        return PchipZero(times, zeros, extrapolation)
    raise InterpolationError(
        f"Unknown interpolation method '{method}'; supported: {INTERPOLATION_METHODS}."
    )
