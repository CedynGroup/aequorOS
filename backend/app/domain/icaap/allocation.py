"""Allocating internal capital to business units, with nothing lost in rounding.

Allocation is a presentation of a decision, not a new calculation: the line
totals are already determined, and this module only splits them. Two properties
make the split defensible, and both are tested:

* **the parts sum to the whole, exactly.** Dividing a total three ways in
  decimal money does not add back up, so the remainder is distributed by the
  largest-remainder rule (one quantum each to the largest fractional parts,
  ties broken by unit key) rather than dropped into a rounding line nobody can
  explain to a committee.
* **the answer does not depend on the order** units were entered in.

Drivers are per line and of one kind: RWA share, exposure share, or manual
percentages that must total exactly one hundred. Mixing kinds within a line
would mean summing weights that are not in the same unit, so it is refused.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal
from typing import Literal

from app.domain.icaap.units import AMOUNT_QUANTUM, HUNDRED, ZERO, amount

DriverKind = Literal["rwa_share", "exposure_share", "manual_pct"]
MANUAL_PCT: DriverKind = "manual_pct"

AllocationErrorCode = Literal[
    "drivers_all_zero",
    "manual_pct_not_hundred",
    "mixed_driver_kinds",
    "negative_driver",
    "unknown_line",
    "negative_total",
]


class AllocationError(ValueError):
    """An allocation that would not add up, or would invent a weight."""

    def __init__(self, code: AllocationErrorCode, *, line_key: str | None = None):
        self.code: AllocationErrorCode = code
        self.line_key = line_key
        super().__init__(f"{code}:{line_key}" if line_key else code)


@dataclass(frozen=True)
class Driver:
    """One unit's weight on one risk line."""

    unit_key: str
    risk_line_key: str
    kind: DriverKind
    value: Decimal


@dataclass(frozen=True)
class _Share:
    """One unit's floored share of a line, and the part rounding dropped."""

    unit_key: str
    floored: Decimal
    remainder: Decimal


@dataclass(frozen=True)
class AllocationResult:
    amounts: Mapping[tuple[str, str], Decimal]
    unit_totals: Mapping[str, Decimal]
    line_totals: Mapping[str, Decimal]


def allocate(line_totals: Mapping[str, Decimal], drivers: Sequence[Driver]) -> AllocationResult:
    """Split each line across its units so the parts sum to the line exactly."""
    by_line: dict[str, list[Driver]] = {}
    for driver in drivers:
        if driver.risk_line_key not in line_totals:
            raise AllocationError("unknown_line", line_key=driver.risk_line_key)
        if driver.value < ZERO:
            raise AllocationError("negative_driver", line_key=driver.risk_line_key)
        by_line.setdefault(driver.risk_line_key, []).append(driver)

    amounts: dict[tuple[str, str], Decimal] = {}
    unit_totals: dict[str, Decimal] = {}
    resolved_lines: dict[str, Decimal] = {}

    for line_key, line_drivers in sorted(by_line.items()):
        total = line_totals[line_key]
        if total < ZERO:
            raise AllocationError("negative_total", line_key=line_key)
        kinds = {driver.kind for driver in line_drivers}
        if len(kinds) > 1:
            raise AllocationError("mixed_driver_kinds", line_key=line_key)
        weight_total = sum((driver.value for driver in line_drivers), ZERO)
        if weight_total <= ZERO:
            raise AllocationError("drivers_all_zero", line_key=line_key)
        if MANUAL_PCT in kinds and weight_total != HUNDRED:
            raise AllocationError("manual_pct_not_hundred", line_key=line_key)

        shares: list[_Share] = []
        floored_total = ZERO
        for driver in line_drivers:
            raw = total * driver.value / weight_total
            quanta = (raw / AMOUNT_QUANTUM).to_integral_value(rounding=ROUND_FLOOR)
            floored_amount = quanta * AMOUNT_QUANTUM
            shares.append(_Share(driver.unit_key, floored_amount, raw - floored_amount))
            floored_total += floored_amount

        residual_quanta = int(((total - floored_total) / AMOUNT_QUANTUM).to_integral_value())
        ranked = sorted(shares, key=lambda share: (-share.remainder, share.unit_key))
        bonus = {share.unit_key: AMOUNT_QUANTUM for share in ranked[:residual_quanta]}

        line_sum = ZERO
        for share in shares:
            value = amount(share.floored + bonus.get(share.unit_key, ZERO))
            amounts[(share.unit_key, line_key)] = value
            unit_totals[share.unit_key] = unit_totals.get(share.unit_key, ZERO) + value
            line_sum += value
        resolved_lines[line_key] = amount(line_sum)

    return AllocationResult(
        amounts=amounts,
        unit_totals={key: amount(value) for key, value in unit_totals.items()},
        line_totals=resolved_lines,
    )


__all__ = [
    "MANUAL_PCT",
    "AllocationError",
    "AllocationErrorCode",
    "AllocationResult",
    "Driver",
    "DriverKind",
    "allocate",
]
