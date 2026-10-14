"""``behavioral_assumptions`` reference row shape at the shared ingestion boundary."""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping

from . import ReferenceSchema, register

_DECLARED = ReferenceSchema(
    kind="behavioral_assumptions",
    description="behavioral assumptions",
    required=("product_code", "assumption_type", "value"),
    optional=("assumption",),
    numeric=("value",),
)
_DOCUMENTED = dataclasses.replace(_DECLARED, required=("product_code", "assumption", "value"))


def validate_row(row: Mapping[str, object]) -> list[str]:
    schema = _DOCUMENTED if "assumption" in row and "assumption_type" not in row else _DECLARED
    return schema.validate_row(row)


SCHEMA = register(dataclasses.replace(_DECLARED, row_validator=validate_row))
