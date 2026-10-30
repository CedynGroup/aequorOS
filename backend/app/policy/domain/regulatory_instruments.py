"""Publication status and commencement of a regulatory instrument."""

from collections.abc import Mapping
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Literal

from app.policy.domain.ratios import classify_capital_ratio, classify_ratio

type InstrumentStatus = Literal["in_force", "final_not_in_force", "exposure_draft", "unpublished"]


def instrument_status_on(
    status: InstrumentStatus, effective_from: date | None, as_of: date
) -> InstrumentStatus:
    """Dates can commence a final instrument, never finalise a draft."""
    if status in ("exposure_draft", "unpublished"):
        return status
    if effective_from is not None:
        return "in_force" if as_of >= effective_from else "final_not_in_force"
    return status


def instrument_status_label(status: InstrumentStatus, effective_from: date | None) -> str:
    """The status disclosure carried by a generated return and its exports."""
    effective = effective_from.isoformat() if effective_from is not None else None
    if status == "exposure_draft":
        return (
            f"Exposure draft; effective {effective} if final; preparation only"
            if effective
            else "Exposure draft; preparation only"
        )
    if status == "unpublished":
        return "Unpublished instrument; Basel reference; no filing obligation"
    if status == "final_not_in_force":
        return (
            f"Final, not yet in force; effective {effective}"
            if effective
            else "Final, not yet in force"
        )
    return f"In force from {effective}" if effective else "In force"


def forecast_rule_applicable(rule_id: str, liquidity_in_force: bool) -> bool:
    return liquidity_in_force or rule_id not in (
        "year5_lcr_above_minimum",
        "year5_nsfr_above_minimum",
    )


def forecast_status(
    metrics: Mapping[str, object],
    liquidity_in_force: bool,
    legacy_thresholds: Mapping[str, Decimal] | None = None,
) -> Literal["green", "amber", "red", "na"]:
    ratios = ("car", "lcr", "nsfr") if liquidity_in_force else ("car",)
    statuses: list[object] = []
    for ratio in ratios:
        recorded = metrics.get(f"year5_{ratio}_status")
        if recorded is None and legacy_thresholds is not None:
            raw = metrics.get(f"year5_{ratio}_pct")
            minimum = legacy_thresholds.get(f"{ratio}_min")
            if (
                isinstance(raw, (str, int, float, Decimal))
                and not isinstance(raw, bool)
                and minimum is not None
            ):
                try:
                    value = Decimal(str(raw))
                except InvalidOperation:
                    value = Decimal("NaN")
                if value.is_finite():
                    if ratio == "car":
                        recorded = classify_capital_ratio(value, minimum)
                    elif (amber := legacy_thresholds.get("lcr_amber_floor")) is not None:
                        recorded = classify_ratio(value, minimum, amber)
        statuses.append(recorded)
    if "red" in statuses:
        return "red"
    if "amber" in statuses:
        return "amber"
    return "green" if all(value == "green" for value in statuses) else "na"
