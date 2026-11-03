"""fx_rates_current: retain both consumed fields and the documented pair/rate shape.

Validation does not rename the persisted payload or invent a currency conversion.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping

from . import ReferenceSchema, register

_DECLARED = ReferenceSchema(
    kind="fx_rates_current",
    description="Foreign exchange reference quotes",
    required=("currency", "spot_rate", "quote_date"),
    optional=("pair", "rate", "quote_date"),
    numeric=("spot_rate", "rate"),
    dates=("quote_date",),
)
_DOCUMENTED = dataclasses.replace(_DECLARED, required=("pair", "rate", "quote_date"))


def validate_row(row: Mapping[str, object]) -> list[str]:
    # A partially supplied consumed shape must not be hidden by the legacy shape.
    schema = _DECLARED if "currency" in row or "spot_rate" in row else _DOCUMENTED
    return schema.validate_row(row)


SCHEMA = register(dataclasses.replace(_DECLARED, row_validator=validate_row))
