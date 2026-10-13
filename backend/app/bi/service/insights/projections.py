"""Forward-looking statements, typed so they can never be read as observation.

A projection is not a fact about the bank. It is a statement about a TREND
extended past the last date the bank has data for, and everything in this module
exists to keep those two apart: the result type is :class:`Projection`, the
insight it becomes carries the statement class ``projection``, and the copy is
written in the conditional. Nothing here ever lands in the observed-value
vocabulary.

Method
------
Ordinary least squares over ``(days since the first observation, value)``,
computed in :class:`~fractions.Fraction` so the slope and intercept are exact
rationals and only the reported figures are rounded. The method is recorded on
the result (:data:`METHOD`) rather than implied, and so is the assumption it
rests on, because a reader is entitled to know that the line is the observed
trend continued unchanged and nothing more.

When it refuses
---------------
A projection is only as honest as the series under it, so the refusals are
deliberately blunt and each one answers :class:`ProjectionUnavailable` rather
than a number:

* fewer than :data:`MIN_OBSERVATIONS` points — two points are a line through
  themselves, not a trend;
* ANY missing value inside the window — treating a gap as zero, or dropping it
  and pretending the remaining points are consecutive, is exactly the "missing
  data is never zero" defect one layer up (D-042 / D-047);
* every observation on the same date — there is no elapsed time to project
  along;
* a horizon on or before the last observation — that is not a projection.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from fractions import Fraction
from typing import Literal

__all__ = [
    "MIN_OBSERVATIONS",
    "METHOD",
    "Observation",
    "Projection",
    "ProjectionUnavailable",
    "project",
]

#: The fewest points a trend may be drawn through.
MIN_OBSERVATIONS = 3

#: Named on every result: a projection must say how it was made.
METHOD: Literal["least_squares_trend"] = "least_squares_trend"

#: Production copy for what the projection rests on.
ASSUMPTION = "assumes the trend in the observed figures continues unchanged"

_DEFAULT_QUANTUM = Decimal("0.0001")


@dataclass(frozen=True, slots=True)
class Observation:
    """One point of the series a projection is drawn through.

    ``value`` is ``None`` when the measure had no answer on that date. The point
    is kept rather than dropped precisely so :func:`project` can refuse.
    """

    as_of: date
    value: Decimal | None


@dataclass(frozen=True, slots=True)
class Projection:
    """Where the observed trend reaches at ``horizon``, and on what basis."""

    measure_id: str
    method: str
    horizon: date
    value: Decimal
    change_from_last: Decimal
    per_day_change: Decimal
    window_start: date
    window_end: date
    observation_count: int
    assumption: str
    quantum: Decimal


ProjectionUnavailableReason = Literal[
    "too_few_observations",
    "observation_missing",
    "no_elapsed_time",
    "horizon_not_forward",
]


@dataclass(frozen=True, slots=True)
class ProjectionUnavailable:
    """Why no projection was made. Never a zero, never a flat line."""

    measure_id: str
    reason: ProjectionUnavailableReason


def _quantise(value: Fraction, quantum: Decimal) -> Decimal:
    return Decimal(round(value / Fraction(quantum))) * quantum


def project(
    measure_id: str,
    observations: Sequence[Observation],
    *,
    horizon: date,
    quantum: Decimal = _DEFAULT_QUANTUM,
) -> Projection | ProjectionUnavailable:
    """Extend the observed trend to ``horizon``, or say why it cannot be done."""
    ordered = sorted(observations, key=lambda point: point.as_of)
    if len(ordered) < MIN_OBSERVATIONS:
        return ProjectionUnavailable(measure_id, "too_few_observations")
    if any(point.value is None for point in ordered):
        return ProjectionUnavailable(measure_id, "observation_missing")
    start, end = ordered[0].as_of, ordered[-1].as_of
    if start == end:
        return ProjectionUnavailable(measure_id, "no_elapsed_time")
    if horizon <= end:
        return ProjectionUnavailable(measure_id, "horizon_not_forward")

    days = [Fraction((point.as_of - start).days) for point in ordered]
    values = [Fraction(point.value) for point in ordered if point.value is not None]
    count = Fraction(len(ordered))
    mean_day = sum(days, Fraction(0)) / count
    mean_value = sum(values, Fraction(0)) / count
    variance = sum(((day - mean_day) ** 2 for day in days), Fraction(0))
    covariance = sum(
        ((day - mean_day) * (value - mean_value) for day, value in zip(days, values, strict=True)),
        Fraction(0),
    )
    if variance == 0:  # every point on one date, already excluded above
        return ProjectionUnavailable(measure_id, "no_elapsed_time")
    slope = covariance / variance
    intercept = mean_value - slope * mean_day
    projected = intercept + slope * Fraction((horizon - start).days)
    last_value = values[-1]
    return Projection(
        measure_id=measure_id,
        method=METHOD,
        horizon=horizon,
        value=_quantise(projected, quantum),
        change_from_last=_quantise(projected - last_value, quantum),
        per_day_change=_quantise(slope, quantum),
        window_start=start,
        window_end=end,
        observation_count=len(ordered),
        assumption=ASSUMPTION,
        quantum=quantum,
    )
