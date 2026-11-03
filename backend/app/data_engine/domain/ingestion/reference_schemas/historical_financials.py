"""historical_financials: validate consumed and documented historical row shapes.

Both are retained verbatim; accepting a documented shape does not make a reader
consume fields it does not understand.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping

from . import ReferenceSchema, register

_DECLARED = ReferenceSchema(
    kind="historical_financials",
    description="Historical observations supplied by the bank",
    required=("period_end", "net_interest_income_ghs"),
    optional=(
        "month",
        "total_assets",
        "net_income",
        "non_interest_income_ghs",
        "operating_expenses_ghs",
        "provisions_ghs",
        "net_income_ghs",
    ),
    numeric=(
        "net_interest_income_ghs",
        "non_interest_income_ghs",
        "operating_expenses_ghs",
        "provisions_ghs",
        "net_income_ghs",
        "total_assets",
        "net_income",
    ),
    dates=("period_end", "month"),
)
_DOCUMENTED = dataclasses.replace(_DECLARED, required=("month", "total_assets", "net_income"))


def validate_row(row: Mapping[str, object]) -> list[str]:
    schema = _DECLARED if "period_end" in row or "net_interest_income_ghs" in row else _DOCUMENTED
    return schema.validate_row(row)


SCHEMA = register(dataclasses.replace(_DECLARED, row_validator=validate_row))
