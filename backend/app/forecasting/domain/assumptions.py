"""The forecast assumption set a bank governs: its scenarios, keys and admissible values.

Pure: no database, no tenant. One governed version carries a COMPLETE set, every
preset scenario with every projection driver, so a version is a self-contained
board decision and a run never mixes values from two approvals.

The engine defaults for fee income, tax rate and the securities shift are not
part of the set: the presets never carried them, and a run resolves them from the
engine unless the analyst overrides them for one run.

The governance itself — maker-checker approval and effective dating of the
assumptions a projection rests on — follows the BCBS Stress testing principles
(October 2018), Principle 2 (governance, including senior management and board
oversight of scenarios and assumptions) and Principle 8 (assumptions subject to
challenge and regular review).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Final

PRESET_CODES: Final = ("base", "adverse", "severely_adverse")

ASSUMPTION_KEYS: Final = (
    "loan_growth_pct",
    "deposit_growth_pct",
    "nim_pct",
    "cost_to_income_pct",
    "credit_loss_rate_pct",
    "fx_depreciation_pct",
    "dividend_payout_pct",
)

_HUNDRED = Decimal(100)
_SANITY = Decimal(1000)

#: Inclusive bounds per driver. The lower bounds are arithmetic, not policy: a
#: book cannot shrink by more than all of it, a payout or a loss rate cannot be
#: negative, and a payout above 100% of earnings is not a payout ratio. The
#: upper bound elsewhere only rejects a typing slip (a value in basis points
#: entered as a percentage); it is not a calibration.
BOUNDS: Final[Mapping[str, tuple[Decimal, Decimal]]] = {
    "loan_growth_pct": (-_HUNDRED, _SANITY),
    "deposit_growth_pct": (-_HUNDRED, _SANITY),
    "nim_pct": (-_HUNDRED, _HUNDRED),
    "cost_to_income_pct": (Decimal(0), _SANITY),
    "credit_loss_rate_pct": (Decimal(0), _HUNDRED),
    "fx_depreciation_pct": (-_HUNDRED, _SANITY),
    "dividend_payout_pct": (Decimal(0), _HUNDRED),
}

#: The starting position a newly provisioned bank is offered as a DRAFT. It is
#: illustrative, calibrated to no institution, and never resolves for a run:
#: forecasting stays not computable until a maker submits it (or their own
#: revision) and a different checker approves it.
STARTING_POSITION: Final[Mapping[str, Mapping[str, str]]] = {
    "base": {
        "loan_growth_pct": "18",
        "deposit_growth_pct": "16",
        "nim_pct": "4.8",
        "cost_to_income_pct": "48",
        "credit_loss_rate_pct": "1.0",
        "fx_depreciation_pct": "0",
        "dividend_payout_pct": "30",
    },
    "adverse": {
        "loan_growth_pct": "8",
        "deposit_growth_pct": "6",
        "nim_pct": "4.2",
        "cost_to_income_pct": "54",
        "credit_loss_rate_pct": "1.5",
        "fx_depreciation_pct": "15",
        "dividend_payout_pct": "0",
    },
    "severely_adverse": {
        "loan_growth_pct": "-2",
        "deposit_growth_pct": "-8",
        "nim_pct": "3.6",
        "cost_to_income_pct": "60",
        "credit_loss_rate_pct": "2.0",
        "fx_depreciation_pct": "40",
        "dividend_payout_pct": "0",
    },
}

type PresetValues = dict[str, dict[str, Decimal]]


@dataclass(frozen=True)
class AssumptionProblem:
    """One reason a proposed set is not admissible, addressed to its field."""

    scenario_code: str
    assumption_key: str | None
    message: str


class InvalidAssumptionSet(ValueError):
    """The proposed set is incomplete, carries an unknown entry, or is out of range."""

    def __init__(self, problems: list[AssumptionProblem]) -> None:
        super().__init__("; ".join(problem.message for problem in problems))
        self.problems = problems


def validate_presets(presets: Mapping[str, Mapping[str, object]]) -> PresetValues:
    """Return the set as Decimals, or raise naming every problem at once.

    Complete means every scenario in :data:`PRESET_CODES` with every driver in
    :data:`ASSUMPTION_KEYS`; anything else in the payload is refused rather than
    ignored, so a typo never becomes a silently dropped assumption.
    """
    problems: list[AssumptionProblem] = [
        AssumptionProblem(code, None, f"'{code}' is not a forecast scenario.")
        for code in presets
        if code not in PRESET_CODES
    ]
    values: PresetValues = {}
    for code in PRESET_CODES:
        scenario = presets.get(code)
        if scenario is None:
            problems.append(AssumptionProblem(code, None, f"The '{code}' scenario is missing."))
            continue
        problems += [
            AssumptionProblem(code, key, f"'{key}' is not a forecast assumption.")
            for key in scenario
            if key not in ASSUMPTION_KEYS
        ]
        values[code] = {}
        for key in ASSUMPTION_KEYS:
            parsed = _decimal(scenario.get(key))
            if parsed is None:
                problems.append(
                    AssumptionProblem(code, key, f"'{code}' {key} needs a numeric value.")
                )
                continue
            low, high = BOUNDS[key]
            if not low <= parsed <= high:
                problems.append(
                    AssumptionProblem(
                        code, key, f"'{code}' {key} must be between {low} and {high}."
                    )
                )
                continue
            values[code][key] = parsed
    if problems:
        raise InvalidAssumptionSet(problems)
    return values


def _decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except InvalidOperation:
        return None
    return parsed if parsed.is_finite() else None
