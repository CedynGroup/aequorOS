"""Reference resolution (REFERENCE_RESOLVE operation type).

Resolves a bank's own product code to the canonical regulatory category declared in the
per-institution :class:`~app.domain.ingestion.contracts.MappingConfig.product_mappings`.
The resolution is an *enrichment*, not a rewrite of a regulatory-critical field: the source
``product_code`` is preserved and the canonical category is attached under a derived,
non-critical key (:data:`RESOLVED_CATEGORY_FIELD`) that the downstream translate step reads.
This keeps the resolution fully audited (a SANCTIONED :class:`ETLOperation` with confidence
and lineage) without ever emitting a SANCTIONED op on the critical ``regulatory_category``
concept — which the contract guard forbids.

A product code with no mapping entry is FLAGGED (not dropped): an unmapped product is a
data-quality gap for a human to resolve, exactly the kind of imperfection the layer surfaces.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from app.etl.contracts import (
    Disposition,
    ETLOperation,
    ETLOperationType,
    ETLProvenance,
    Preprocessor,
)
from app.etl.deduplication._fields import record_id as _record_id
from app.etl.resolve import make_operation, resolve_concept

if TYPE_CHECKING:
    from app.domain.ingestion.contracts import MappingConfig, RawRecord

_OPERATION_REF = "reference_resolution/v1"

# Derived, non-critical key that carries the resolved canonical category on the cleaned
# record. Deliberately not the critical ``regulatory_category`` concept name.
RESOLVED_CATEGORY_FIELD = "resolved_product_category"


class ReferenceResolver(Preprocessor):
    """Resolve source product codes to canonical regulatory categories via the mapping."""

    operation_type = ETLOperationType.REFERENCE_RESOLVE

    def __init__(self, product_mappings: dict[str, str]) -> None:
        # Case-insensitive lookup: banks are inconsistent about product-code casing.
        self._mappings = {
            code.strip().upper(): category for code, category in product_mappings.items()
        }

    def apply(self, record: RawRecord) -> list[ETLOperation]:
        if record.entity_type not in {"product", "position"}:
            return []
        source_field = _find_product_code_field(record)
        if source_field is None:
            return []
        raw_code = record.data.get(source_field)
        if raw_code is None or str(raw_code).strip() == "":
            return []

        rid = _record_id(record)
        code = str(raw_code).strip().upper()
        category = self._mappings.get(code)
        if category is None:
            return [self._flag_unmapped(rid, source_field, raw_code)]

        # Enrichment onto a non-critical derived field: SANCTIONED, fully audited.
        op = make_operation(
            record_id=rid,
            source_field=RESOLVED_CATEGORY_FIELD,
            before=record.data.get(RESOLVED_CATEGORY_FIELD),
            after=category,
            operation_type=ETLOperationType.REFERENCE_RESOLVE,
            operation_ref=_OPERATION_REF,
            value_preserving=False,
            lineage_input_ids=(record.source_locator,),
        )
        return [op] if op is not None else []

    def _flag_unmapped(self, rid: str, source_field: str, raw_code: object) -> ETLOperation:
        return ETLOperation(
            record_id=rid,
            field_name=source_field,
            disposition=Disposition.FLAGGED,
            before=raw_code,
            after=None,
            provenance=ETLProvenance(
                operation_type=ETLOperationType.REFERENCE_RESOLVE,
                operation_ref=_OPERATION_REF,
                confidence=1.0,
            ),
            lineage_input_ids=(source_field,),
            reason=(
                f"product code {raw_code!r} has no entry in the institution product mapping; "
                f"its canonical regulatory category cannot be resolved and needs onboarding review."
            ),
        )


def _find_product_code_field(record: RawRecord) -> str | None:
    """The source column carrying the product code, if any (concept ``product_code``)."""
    for source_field in record.data:
        concept = resolve_concept(source_field)
        if concept == "product_code" or _normalized(source_field) == "productcode":
            return source_field
    return None


def _normalized(source_field: str) -> str:
    return re.sub(r"[^a-z0-9]", "", source_field.lower())


def build_reference_resolver(mapping: MappingConfig) -> ReferenceResolver:
    """Construct a resolver from a :class:`MappingConfig`'s ``product_mappings``."""
    return ReferenceResolver(dict(mapping.product_mappings))
