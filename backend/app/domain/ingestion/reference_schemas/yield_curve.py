"""``yield_curve`` reference row shape at the shared ingestion boundary."""

from __future__ import annotations

from . import ReferenceSchema, register

SCHEMA = register(
    ReferenceSchema(
        kind="yield_curve",
        description="yield curve",
        required=("curve_name", "currency", "tenor_months", "rate", "quote_date"),
        numeric=("tenor_months", "rate"),
        dates=("quote_date",),
    )
)
