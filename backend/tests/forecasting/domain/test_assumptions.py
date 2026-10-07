"""The admissible forecast assumption set: complete, known and in range, or refused whole."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.forecasting.domain.assumptions import (
    ASSUMPTION_KEYS,
    PRESET_CODES,
    STARTING_POSITION,
    InvalidAssumptionSet,
    validate_presets,
)


def _complete() -> dict[str, dict[str, object]]:
    return {code: dict(values) for code, values in STARTING_POSITION.items()}


def test_the_starting_position_is_a_complete_admissible_set() -> None:
    values = validate_presets(_complete())

    assert list(values) == list(PRESET_CODES)
    assert all(list(values[code]) == list(ASSUMPTION_KEYS) for code in PRESET_CODES)
    assert values["base"]["nim_pct"] == Decimal("4.8")
    assert values["severely_adverse"]["loan_growth_pct"] == Decimal("-2")


def test_every_problem_is_named_at_once() -> None:
    presets = _complete()
    del presets["adverse"]
    del presets["base"]["nim_pct"]
    presets["base"]["dividend_payout_pct"] = "120"
    presets["base"]["typo_pct"] = "1"
    presets["stagflation"] = {}

    with pytest.raises(InvalidAssumptionSet) as raised:
        validate_presets(presets)

    problems = {(p.scenario_code, p.assumption_key): p.message for p in raised.value.problems}
    assert problems == {
        ("stagflation", None): "'stagflation' is not a forecast scenario.",
        ("adverse", None): "The 'adverse' scenario is missing.",
        ("base", "typo_pct"): "'typo_pct' is not a forecast assumption.",
        ("base", "nim_pct"): "'base' nim_pct needs a numeric value.",
        ("base", "dividend_payout_pct"): "'base' dividend_payout_pct must be between 0 and 100.",
    }


@pytest.mark.parametrize(
    ("key", "value", "admissible"),
    [
        ("loan_growth_pct", "-100", True),
        ("loan_growth_pct", "-100.01", False),
        ("credit_loss_rate_pct", "-0.1", False),
        ("cost_to_income_pct", "130", True),
        ("nim_pct", "NaN", False),
        ("nim_pct", "Infinity", False),
        ("nim_pct", True, False),
        ("nim_pct", "4.85", True),
    ],
)
def test_bounds_and_finiteness(key: str, value: object, admissible: bool) -> None:
    presets = _complete()
    presets["base"][key] = value

    if admissible:
        assert validate_presets(presets)["base"][key] == Decimal(str(value))
    else:
        with pytest.raises(InvalidAssumptionSet):
            validate_presets(presets)
