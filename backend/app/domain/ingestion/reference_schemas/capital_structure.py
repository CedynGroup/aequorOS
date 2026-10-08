"""``capital_structure`` — the bank's regulatory capital register.

One row per capital component and reporting date: ``capital_component`` names
the item (``paid_up_capital``, ``subordinated_debt`` …), ``amount_ghs`` is its
reporting-currency amount (negative, or a ``*_DEDUCTION`` tier, for a
deduction), and ``tier`` places it in CET1, AT1 or Tier 2.

**The tier is a closed vocabulary.** It decides which ratio a component counts
toward, so a value the platform does not recognise is refused at the door
rather than read as CET1 (:func:`..capital_tiers.parse_capital_tier`).

Docs: docs/API_INTEGRATION.md §3.5.
"""

from __future__ import annotations

import dataclasses
from typing import Protocol, cast

from app.domain.ingestion.capital_tiers import DOCUMENTED_TIERS, parse_capital_tier

from . import ReferenceSchema, register


class _DeclarativeChecks(Protocol):
    """``ReferenceSchema.validate_row`` with the row typed (the base leaves it bare)."""

    def validate_row(self, row: dict[str, object], /) -> list[str]: ...


_DECLARED = ReferenceSchema(
    kind="capital_structure",
    description=(
        "Regulatory capital register: each capital component, its amount and its "
        "CET1 / AT1 / Tier 2 placement"
    ),
    grain="one row per capital component per reporting date",
    required=("capital_component", "amount_ghs", "tier"),
    numeric=("amount_ghs",),
)


def validate_capital_structure_row(row: dict[str, object]) -> list[str]:
    """Schema problems plus the tier vocabulary rule."""
    problems = cast(_DeclarativeChecks, _DECLARED).validate_row(row)
    tier = row.get("tier")
    if tier not in (None, "") and parse_capital_tier(tier) is None:
        problems.append(
            f"field 'tier' must be one of {list(DOCUMENTED_TIERS)} (got {tier!r}); an "
            "unrecognised tier is refused rather than counted as CET1"
        )
    return problems


# The ingestion path asks ``problems_for``, which runs only the row validator, so
# the validator calls the declarative half itself (see ``business_units``).
SCHEMA = register(dataclasses.replace(_DECLARED, row_validator=validate_capital_structure_row))
