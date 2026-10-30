"""historical_cashflows: validate consumed and documented historical row shapes.

Both are retained verbatim; accepting a documented shape does not make a reader
consume fields it does not understand.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping

from . import ReferenceSchema, register

_DECLARED = ReferenceSchema(
    kind="historical_cashflows",
    description="Historical observations supplied by the bank",
    required=("date", "deposit_inflow_ghs", "deposit_outflow_ghs"),
    optional=("inflow", "outflow", "net_cashflow_ghs"),
    numeric=("deposit_inflow_ghs", "deposit_outflow_ghs", "net_cashflow_ghs", "inflow", "outflow"),
    dates=("date",),
)
_DOCUMENTED = dataclasses.replace(_DECLARED, required=("date", "inflow", "outflow"))


def validate_row(row: Mapping[str, object]) -> list[str]:
    schema = (
        _DECLARED if "deposit_inflow_ghs" in row or "deposit_outflow_ghs" in row else _DOCUMENTED
    )
    return schema.validate_row(row)


SCHEMA = register(dataclasses.replace(_DECLARED, row_validator=validate_row))
