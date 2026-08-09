"""Forward-curve derivation and the pre-publish QA gate (spec §5 step 6).

Forwards magnify every bootstrap defect (a zero rate is the *average* of
instantaneous forwards), so the spec makes forward smoothness the acid test of
a build and the QA gate a **hard pre-publish check**: positive forwards, no
oscillation. This module derives the forward curve on a grid and scores it.

Oscillation measures (both reported; the ratio carries the pass/fail):

- ``slope_sign_changes`` — how many times the forward slope flips sign over
  the grid. A single hump is 1; sawtooth ringing is many.
- ``total_variation_ratio`` — total variation of the forwards divided by their
  range (max - min). Exactly 1.0 for a monotone forward path, up to 2.0 for a
  single clean hump, and growing without bound as the path oscillates. A flat
  path (range ~ 0) scores 1.0 by convention. The gate passes when the ratio is
  at or below ``oscillation_tolerance`` (default 3.0 — admits one hump plus
  normal boundary curvature, rejects ringing).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.domain.curves.bootstrap import ZeroCurve

__all__ = ["ForwardCurve", "ForwardQaResult", "QaError", "forward_curve", "qa_forwards"]

_FLAT_RANGE = 1e-12


class QaError(ValueError):
    """The QA grid is malformed (too short, unsorted, negative times)."""


@dataclass(frozen=True)
class ForwardCurve:
    """Instantaneous forwards on a grid plus the period (grid-to-grid) forwards.

    ``period_forwards[i]`` is the continuously compounded forward between
    ``times[i]`` and ``times[i+1]`` (one fewer entry than the grid).
    """

    times: tuple[float, ...]
    instantaneous: tuple[float, ...]
    period_forwards: tuple[float, ...]


@dataclass(frozen=True)
class ForwardQaResult:
    """Structured verdict of the pre-publish forward QA gate."""

    min_forward: float
    positivity_required: bool
    positivity_pass: bool
    slope_sign_changes: int
    total_variation: float
    total_variation_ratio: float
    oscillation_tolerance: float
    oscillation_pass: bool
    passed: bool


def _validated_grid(grid: Sequence[float]) -> tuple[float, ...]:
    times = tuple(float(t) for t in grid)
    if len(times) < 3:
        raise QaError("A forward grid needs at least three points.")
    if times[0] < 0.0:
        raise QaError("Grid times must be non-negative.")
    for left, right in zip(times, times[1:], strict=False):
        if right <= left:
            raise QaError("Grid times must be strictly increasing.")
    return times


def forward_curve(zero_curve: ZeroCurve, grid: Sequence[float]) -> ForwardCurve:
    """Derive instantaneous and period forwards from a zero curve on a grid."""
    times = _validated_grid(grid)
    instantaneous = tuple(zero_curve.instantaneous_forward(t) for t in times)
    period = tuple(
        zero_curve.forward(t1, t2) for t1, t2 in zip(times, times[1:], strict=False)
    )
    return ForwardCurve(times=times, instantaneous=instantaneous, period_forwards=period)


def qa_forwards(
    curve: ZeroCurve,
    grid: Sequence[float],
    positivity: bool = True,
    oscillation_tolerance: float = 3.0,
) -> ForwardQaResult:
    """Run the hard pre-publish QA gate on the curve's instantaneous forwards."""
    if oscillation_tolerance < 1.0:
        raise QaError("oscillation_tolerance below 1.0 rejects even monotone forwards.")
    forwards = forward_curve(curve, grid).instantaneous
    minimum = min(forwards)
    positivity_pass = (not positivity) or minimum > 0.0

    slopes = [b - a for a, b in zip(forwards, forwards[1:], strict=False)]
    sign_changes = 0
    previous_sign = 0
    for slope in slopes:
        if slope == 0.0:
            continue
        sign = 1 if slope > 0.0 else -1
        if previous_sign not in (0, sign):
            sign_changes += 1
        previous_sign = sign
    total_variation = sum(abs(slope) for slope in slopes)
    value_range = max(forwards) - min(forwards)
    ratio = 1.0 if value_range < _FLAT_RANGE else total_variation / value_range
    oscillation_pass = ratio <= oscillation_tolerance

    return ForwardQaResult(
        min_forward=minimum,
        positivity_required=positivity,
        positivity_pass=positivity_pass,
        slope_sign_changes=sign_changes,
        total_variation=total_variation,
        total_variation_ratio=ratio,
        oscillation_tolerance=oscillation_tolerance,
        oscillation_pass=oscillation_pass,
        passed=positivity_pass and oscillation_pass,
    )
