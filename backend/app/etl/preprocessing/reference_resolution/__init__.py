"""Reference resolution (institution product code -> canonical regulatory category)."""

from __future__ import annotations

from app.etl.preprocessing.reference_resolution.resolver import (
    RESOLVED_CATEGORY_FIELD,
    ReferenceResolver,
    build_reference_resolver,
)

__all__ = ["RESOLVED_CATEGORY_FIELD", "ReferenceResolver", "build_reference_resolver"]
