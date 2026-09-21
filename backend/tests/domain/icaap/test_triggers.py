"""Capital-plan triggers: evaluated at last, against each year's own floor."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.domain.icaap.triggers import (
    TRIGGER_METRICS,
    PathPoint,
    TriggerDef,
    classify,
    evaluate,
    resolve_metric,
)
from app.domain.policy import Direction

YEAR_ONE = date(2026, 12, 31)
YEAR_TWO = date(2027, 12, 31)
YEAR_THREE = date(2028, 12, 31)
CAR_TRIGGER = TriggerDef(
    metric_code="car", early_warning_level=Decimal(15), action_level=Decimal(14)
)
FLOORS = {"car": {YEAR_ONE: Decimal(13), YEAR_TWO: Decimal(13), YEAR_THREE: Decimal("14.5")}}


def path(*values: str | None) -> dict[str, dict[str, tuple[PathPoint, ...]]]:
    ends = (YEAR_ONE, YEAR_TWO, YEAR_THREE)
    return {
        "car": {
            "baseline": tuple(
                PathPoint(
                    year=index + 1,
                    period_end=end,
                    value=None if raw is None else Decimal(raw),
                )
                for index, (end, raw) in enumerate(zip(ends, values, strict=True))
            )
        }
    }


def test_the_plans_free_text_metric_is_matched_by_alias_or_not_at_all() -> None:
    assert resolve_metric("total_capital_ratio") is TRIGGER_METRICS["car"]
    assert resolve_metric("CAR_PCT") is TRIGGER_METRICS["car"]
    assert resolve_metric("gut feel") is None
    assert resolve_metric(None) is None


def test_an_unknown_metric_is_not_evaluable_and_says_so() -> None:
    result = evaluate(
        [TriggerDef("vibes", Decimal(1), Decimal(0))], current={}, paths={}, floors={}
    )
    assert result.results[0].current_status == "not_evaluable"
    assert result.findings[0].code == "trigger_metric_unknown"


@pytest.mark.parametrize(
    ("value", "status"),
    [
        (Decimal(16), "clear"),
        (Decimal("14.9"), "early_warning"),
        (Decimal("13.9"), "action"),
        (Decimal("12.9"), "regulatory_breach"),
        (None, "not_evaluable"),
    ],
)
def test_a_reading_is_classified_against_the_levels_and_the_floor(
    value: Decimal | None, status: str
) -> None:
    assert classify(Direction.FLOOR, value, CAR_TRIGGER, Decimal(13)) == status


def test_equality_with_a_level_has_not_crossed_it() -> None:
    assert classify(Direction.FLOOR, Decimal(15), CAR_TRIGGER, Decimal(13)) == "clear"
    assert classify(Direction.FLOOR, Decimal(14), CAR_TRIGGER, Decimal(13)) == "early_warning"
    assert classify(Direction.FLOOR, Decimal(13), CAR_TRIGGER, Decimal(13)) == "action"


def test_the_first_year_each_level_is_crossed_is_reported_per_scenario() -> None:
    result = evaluate(
        [CAR_TRIGGER],
        current={"car": Decimal(16)},
        paths=path("16.0", "14.8", "13.5"),
        floors=FLOORS,
    )
    car = result.results[0]
    assert car.current_status == "clear"
    # Year three's own floor is 14.5, so 13.5 in that year is already a breach.
    assert [point.status for point in car.points] == [
        "clear",
        "early_warning",
        "regulatory_breach",
    ]
    assert car.first_crossing["baseline"]["early_warning"] == 2
    assert car.first_crossing["baseline"]["regulatory_breach"] == 3

    without_the_step_up = evaluate(
        [CAR_TRIGGER],
        current={"car": Decimal(16)},
        paths=path("16.0", "14.8", "13.5"),
        floors={"car": {YEAR_ONE: Decimal(13), YEAR_TWO: Decimal(13), YEAR_THREE: Decimal(13)}},
    )
    assert [point.status for point in without_the_step_up.results[0].points] == [
        "clear",
        "early_warning",
        "action",
    ]
    assert without_the_step_up.results[0].first_crossing["baseline"]["action"] == 3


def test_a_projected_year_below_that_years_floor_is_a_breach() -> None:
    """DV-005: the floor that steps up in year three is the one year three uses."""
    result = evaluate(
        [CAR_TRIGGER],
        current={"car": Decimal(16)},
        paths=path("14.0", "14.0", "14.0"),
        floors=FLOORS,
    )
    statuses = [point.status for point in result.results[0].points]
    assert statuses == ["early_warning", "early_warning", "regulatory_breach"]


def test_an_action_level_below_a_projected_floor_is_a_finding() -> None:
    result = evaluate(
        [CAR_TRIGGER],
        current={"car": Decimal(16)},
        paths=path("16.0", "16.0", "16.0"),
        floors=FLOORS,
    )
    codes = [finding.code for finding in result.findings]
    assert codes == ["action_weaker_than_floor"]
    finding = result.findings[0]
    assert finding.year == 3
    assert finding.detail["floor"] == "14.5"
    assert finding.detail["floor_code"] == "car_min"


def test_a_warning_that_fires_after_the_action_is_self_defeating() -> None:
    inverted = TriggerDef("car", early_warning_level=Decimal(13), action_level=Decimal(15))
    result = evaluate(
        [inverted], current={"car": Decimal(16)}, paths=path("16.0", "16.0", "16.0"), floors={}
    )
    assert [finding.code for finding in result.findings] == ["ordering_inconsistent"]


def test_a_metric_with_no_floor_still_evaluates_its_levels() -> None:
    result = evaluate(
        [CAR_TRIGGER], current={"car": Decimal("13.5")}, paths=path("13.5", None, None), floors={}
    )
    assert result.results[0].current_status == "action"
    assert result.results[0].points[1].status == "not_evaluable"


def test_every_catalogue_metric_names_its_governed_floor() -> None:
    for metric in TRIGGER_METRICS.values():
        assert metric.floor_code
        assert metric.direction is Direction.FLOOR
        assert metric.key in metric.aliases
