"""``behavioral_assumptions`` reference row shape at the shared ingestion boundary."""

from __future__ import annotations

from . import ReferenceSchema, register

SCHEMA = register(
    ReferenceSchema(
        kind="behavioral_assumptions",
        description="behavioral assumptions",
        required=("product_code", "assumption", "value"),
        numeric=("value",),
    )
)
