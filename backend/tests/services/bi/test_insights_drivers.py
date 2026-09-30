"""The ratio bridge is exact, and its judgement is the platform's existing one.

Two properties are pinned here and nowhere else:

* the parts of a bridged ratio sum to the whole change EXACTLY, as ``Decimal``
  values — no tolerance is used anywhere in this file, because a bridge that is
  only nearly additive is a bridge a reader cannot check;
* :func:`app.services.bi.insights.drivers.favourability` returns the same
  verdict ``app.services.report_comparison`` returns, for every metric key in
  its registries and in both directions. The BI catalogue restates the
  DIRECTIONS (``catalogue/directions.py``, already parity-tested); this pins
  that the verdict drawn FROM a direction has not drifted either — which is what
  makes D-013's magnitude rule one rule rather than two.
"""

from __future__ import annotations

import random
from decimal import Decimal
from fractions import Fraction

import pytest

from app.domain.bi.catalogue import catalogue
from app.services import report_comparison
from app.services.bi.insights import drivers

_FAVOURABILITY_SPELLING = {
    "favourable": "favorable",
    "adverse": "adverse",
    "neutral": "neutral",
}


def _bridge(  # noqa: PLR0913 - a bridge takes four figures plus its precision
    prior_numerator: str,
    prior_denominator: str,
    current_numerator: str,
    current_denominator: str,
    *,
    quantum: Decimal = drivers.DEFAULT_QUANTUM,
    direction: str = "lower_better",
) -> drivers.RatioBridge:
    built = drivers.ratio_bridge(
        measure_id="loans.npl_ratio_pct",
        numerator_measure_id="loans.npl_exposure_rc",
        denominator_measure_id="loans.classification_exposure_rc",
        numerator_label="Non-performing loans",
        denominator_label="Loans under classification",
        prior_numerator=Decimal(prior_numerator),
        prior_denominator=Decimal(prior_denominator),
        current_numerator=Decimal(current_numerator),
        current_denominator=Decimal(current_denominator),
        direction=direction,  # pyright: ignore[reportArgumentType] - the test states it literally
        value_type="pct",
        quantum=quantum,
    )
    assert isinstance(built, drivers.RatioBridge)
    return built


def test_the_parts_sum_to_the_whole_exactly() -> None:
    bridge = _bridge("100", "1000", "150", "1200")
    assert bridge.change == Decimal("2.5000")
    assert sum(leg.contribution for leg in bridge.legs) == bridge.change
    assert bridge.sums_exactly()


def test_the_parts_are_the_numerator_the_denominator_and_their_co_movement() -> None:
    bridge = _bridge("100", "1000", "150", "1200")
    by_component = {leg.component: leg.contribution for leg in bridge.legs}
    assert by_component["numerator"] == Decimal("5.0000")
    assert by_component["denominator"] == Decimal("-1.6667")
    assert by_component["co_movement"] == Decimal("-0.8333")
    assert sum(by_component.values()) == bridge.change


def test_a_component_that_did_not_move_produces_no_co_movement_part() -> None:
    bridge = _bridge("100", "1000", "150", "1000")
    assert [leg.component for leg in bridge.legs] == ["numerator", "denominator"]
    assert bridge.sums_exactly()


@pytest.mark.parametrize(
    "quantum", [Decimal("1"), Decimal("0.01"), Decimal("0.0001"), Decimal("0.00000001")]
)
def test_exactness_holds_at_every_reporting_precision(quantum: Decimal) -> None:
    bridge = _bridge("1", "3", "2", "7", quantum=quantum)
    assert sum(leg.contribution for leg in bridge.legs) == bridge.change


def test_exactness_holds_over_a_wide_sweep_of_books() -> None:
    """A thousand books, including negatives and near-cancelling moves."""
    generator = random.Random(20260923)
    for _ in range(1000):
        values = [
            Decimal(generator.randint(-5_000_000, 5_000_000)) / Decimal(100) for _ in range(4)
        ]
        if values[1] == 0 or values[3] == 0:
            continue
        bridge = drivers.ratio_bridge(
            measure_id="loans.npl_ratio_pct",
            numerator_measure_id="n",
            denominator_measure_id="d",
            numerator_label="Numerator",
            denominator_label="Denominator",
            prior_numerator=values[0],
            prior_denominator=values[1],
            current_numerator=values[2],
            current_denominator=values[3],
            value_type="pct",
        )
        assert isinstance(bridge, drivers.RatioBridge)
        assert sum(leg.contribution for leg in bridge.legs) == bridge.change


def test_the_reported_change_is_the_rounded_true_change() -> None:
    """Rounding may move the total by at most one quantum, never the identity."""
    bridge = _bridge("1", "3", "2", "7")
    exact = Fraction(100) * (Fraction(2, 7) - Fraction(1, 3))
    assert abs(Fraction(bridge.change) - exact) <= Fraction(drivers.DEFAULT_QUANTUM) / 2


def test_a_ratio_with_no_denominator_is_unavailable_rather_than_zero() -> None:
    unavailable = drivers.ratio_bridge(
        measure_id="loans.npl_ratio_pct",
        numerator_measure_id="n",
        denominator_measure_id="d",
        numerator_label="Numerator",
        denominator_label="Denominator",
        prior_numerator=Decimal(10),
        prior_denominator=Decimal(0),
        current_numerator=Decimal(12),
        current_denominator=Decimal(100),
        value_type="pct",
    )
    assert unavailable == drivers.BridgeUnavailable("loans.npl_ratio_pct", "denominator_zero")


def test_a_missing_component_is_unavailable_rather_than_zero() -> None:
    unavailable = drivers.ratio_bridge(
        measure_id="loans.npl_ratio_pct",
        numerator_measure_id="n",
        denominator_measure_id="d",
        numerator_label="Numerator",
        denominator_label="Denominator",
        prior_numerator=None,
        prior_denominator=Decimal(1000),
        current_numerator=Decimal(12),
        current_denominator=Decimal(1000),
        value_type="pct",
    )
    assert unavailable == drivers.BridgeUnavailable("loans.npl_ratio_pct", "component_missing")


def test_largest_leg_is_the_part_that_moved_the_ratio_most() -> None:
    bridge = _bridge("100", "1000", "150", "1200")
    assert bridge.largest_leg.component == "numerator"


# ---------------------------------------------------------------------------
# the judgement
# ---------------------------------------------------------------------------

_PAIRS: tuple[tuple[str, str], ...] = (
    ("10", "12"),
    ("12", "10"),
    ("10", "10"),
    ("-8", "-12"),
    ("-12", "-8"),
    ("-8", "8"),
    ("-8", "6"),
    ("0", "5"),
)


@pytest.mark.parametrize("left, right", _PAIRS)
@pytest.mark.parametrize(
    "key",
    sorted(
        report_comparison.HIGHER_BETTER
        | report_comparison.LOWER_BETTER
        | report_comparison.MAGNITUDE_LOWER_BETTER
    ),
)
def test_the_verdict_matches_the_comparison_surfaces(key: str, left: str, right: str) -> None:
    prior, current = Decimal(left), Decimal(right)
    direction = report_comparison.favorable_direction(key)
    moved = drivers.move_direction(prior, current)
    expected = report_comparison._favorability(key, prior, current, moved)  # noqa: SLF001
    ours = drivers.favourability(direction, prior, current)
    assert _FAVOURABILITY_SPELLING[ours] == expected


def test_a_magnitude_judged_gain_is_not_reported_as_adverse() -> None:
    """D-013: a signed risk figure that rose toward zero fell in risk terms."""
    assert drivers.favourability("magnitude_lower_better", Decimal("-12"), Decimal("-8")) == (
        "favourable"
    )
    assert drivers.favourability("magnitude_lower_better", Decimal("-8"), Decimal("6")) == (
        "favourable"
    )
    assert drivers.move_direction(Decimal("-12"), Decimal("-8")) == "up"


def test_a_sign_flip_at_equal_magnitude_is_no_change_in_risk() -> None:
    assert drivers.favourability("magnitude_lower_better", Decimal("-8"), Decimal("8")) == (
        "neutral"
    )


def test_an_undeclared_direction_never_guesses() -> None:
    assert drivers.favourability("neutral", Decimal("1"), Decimal("1000")) == "neutral"


def test_every_catalogue_direction_is_one_this_module_understands() -> None:
    for measure in catalogue().measures():
        assert drivers.favourability(measure.favourable_direction, Decimal(1), Decimal(2)) in (
            "favourable",
            "adverse",
            "neutral",
        )


def test_a_percentage_measure_is_bridged_in_points_and_a_ratio_in_units() -> None:
    assert drivers.scale_for("pct") == Fraction(100)
    # `fraction` is the catalogue's name for a ratio; "ratio" is not a
    # `ValueType` at all, so the old assertion tested a value no measure
    # can carry.
    assert drivers.scale_for("fraction") == Fraction(1)
    assert drivers.scale_for("amount") == Fraction(1)


def test_largest_remainder_allocation_preserves_the_total() -> None:
    total = Fraction(1)
    parts = (Fraction(1, 3), Fraction(1, 3), Fraction(1, 3))
    rounded_total, rounded_parts = drivers.quantise_exactly(total, parts, Decimal("0.01"))
    assert sum(rounded_parts) == rounded_total == Decimal("1.00")
