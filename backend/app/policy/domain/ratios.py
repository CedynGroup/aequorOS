"""Pure ratio assessment shared by engines and read-side applicability."""

from decimal import Decimal
from typing import Literal

GREEN_BUFFER_PP = Decimal("0.5")

type RatioStatus = Literal["green", "amber", "red"]


def classify_capital_ratio(value_pct: Decimal, minimum_pct: Decimal) -> RatioStatus:
    """Classify an already-quantized ratio percentage against its minimum.

    Green requires clearing the minimum by ``GREEN_BUFFER_PP`` (0.5pp), which
    makes the CAR green floor exactly the ``car_early_warning`` threshold.
    """
    if value_pct >= minimum_pct + GREEN_BUFFER_PP:
        return "green"
    if value_pct >= minimum_pct:
        return "amber"
    return "red"


def classify_ratio(
    value_pct: Decimal, minimum_pct: Decimal, amber_floor_pct: Decimal
) -> RatioStatus:
    """Classify an already-quantized ratio percentage against its thresholds."""
    if value_pct >= minimum_pct:
        return "green"
    if value_pct >= amber_floor_pct:
        return "amber"
    return "red"
