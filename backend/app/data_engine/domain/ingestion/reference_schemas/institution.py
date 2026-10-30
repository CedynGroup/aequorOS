"""``institution`` reference row shape at the shared ingestion boundary."""

from __future__ import annotations

from . import ReferenceSchema, register

SCHEMA = register(
    ReferenceSchema(
        kind="institution",
        description="institution",
        required=("institution_id", "name"),
        optional=("notes",),
    )
)
