"""Publication status and commencement of a regulatory instrument."""

from collections.abc import Mapping
from datetime import date
from typing import Literal

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
    metrics: Mapping[str, object], liquidity_in_force: bool
) -> Literal["green", "amber", "red", "na"]:
    keys = ("year5_car_status",) + (
        ("year5_lcr_status", "year5_nsfr_status") if liquidity_in_force else ()
    )
    statuses = [metrics.get(key) for key in keys]
    if "red" in statuses:
        return "red"
    if "amber" in statuses:
        return "amber"
    return "green" if all(value == "green" for value in statuses) else "na"
