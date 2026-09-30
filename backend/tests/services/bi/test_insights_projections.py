"""A projection is drawn only when the series supports one, and never guesses.

The refusals matter more than the arithmetic: every one of them is a place where
a forward-looking number would otherwise be invented out of a gap in the data.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.services.bi.insights import projections


def _series(*values: str | None, start: date = date(2026, 1, 31)) -> list[projections.Observation]:
    months = (1, 2, 3, 4, 5, 6)
    return [
        projections.Observation(
            as_of=date(start.year, month, 28),
            value=None if value is None else Decimal(value),
        )
        for month, value in zip(months, values, strict=False)
    ]


def test_a_straight_line_series_projects_along_its_own_line() -> None:
    result = projections.project(
        "engine.car_pct.crd.live",
        [
            projections.Observation(date(2026, 1, 1), Decimal("10")),
            projections.Observation(date(2026, 1, 11), Decimal("11")),
            projections.Observation(date(2026, 1, 21), Decimal("12")),
        ],
        horizon=date(2026, 1, 31),
    )
    assert isinstance(result, projections.Projection)
    assert result.value == Decimal("13.0000")
    assert result.change_from_last == Decimal("1.0000")
    assert result.per_day_change == Decimal("0.1000")
    assert result.method == projections.METHOD
    assert result.observation_count == 3


def test_a_projection_names_its_method_and_its_assumption() -> None:
    result = projections.project(
        "engine.car_pct.crd.live",
        _series("10", "11", "12"),
        horizon=date(2026, 9, 30),
    )
    assert isinstance(result, projections.Projection)
    assert result.assumption == projections.ASSUMPTION
    assert "trend" in result.assumption


def test_two_points_are_not_a_trend() -> None:
    result = projections.project(
        "engine.car_pct.crd.live", _series("10", "11"), horizon=date(2026, 9, 30)
    )
    assert result == projections.ProjectionUnavailable(
        "engine.car_pct.crd.live", "too_few_observations"
    )


def test_a_gap_in_the_series_refuses_rather_than_treating_it_as_zero() -> None:
    result = projections.project(
        "engine.car_pct.crd.live", _series("10", None, "12"), horizon=date(2026, 9, 30)
    )
    assert result == projections.ProjectionUnavailable(
        "engine.car_pct.crd.live", "observation_missing"
    )


def test_a_horizon_inside_the_observed_window_is_not_a_projection() -> None:
    result = projections.project(
        "engine.car_pct.crd.live", _series("10", "11", "12"), horizon=date(2026, 2, 28)
    )
    assert result == projections.ProjectionUnavailable(
        "engine.car_pct.crd.live", "horizon_not_forward"
    )


def test_every_observation_on_one_date_has_no_elapsed_time_to_project_along() -> None:
    same_day = [
        projections.Observation(date(2026, 3, 31), Decimal(value)) for value in ("10", "11", "12")
    ]
    result = projections.project("engine.car_pct.crd.live", same_day, horizon=date(2026, 6, 30))
    assert result == projections.ProjectionUnavailable("engine.car_pct.crd.live", "no_elapsed_time")


def test_a_flat_series_projects_flat_without_inventing_a_direction() -> None:
    result = projections.project(
        "engine.car_pct.crd.live", _series("12", "12", "12"), horizon=date(2026, 9, 30)
    )
    assert isinstance(result, projections.Projection)
    assert result.per_day_change == Decimal("0.0000")
    assert result.change_from_last == Decimal("0.0000")


@pytest.mark.parametrize("quantum", [Decimal("1"), Decimal("0.01"), Decimal("0.000001")])
def test_the_reported_precision_is_the_one_asked_for(quantum: Decimal) -> None:
    result = projections.project(
        "engine.car_pct.crd.live",
        _series("10", "11", "12"),
        horizon=date(2026, 9, 30),
        quantum=quantum,
    )
    assert isinstance(result, projections.Projection)
    assert result.quantum == quantum
    assert result.value == result.value.quantize(quantum)


def test_observations_out_of_order_are_read_in_date_order() -> None:
    ordered = projections.project(
        "engine.car_pct.crd.live", _series("10", "11", "12"), horizon=date(2026, 9, 30)
    )
    shuffled = projections.project(
        "engine.car_pct.crd.live",
        list(reversed(_series("10", "11", "12"))),
        horizon=date(2026, 9, 30),
    )
    assert ordered == shuffled
