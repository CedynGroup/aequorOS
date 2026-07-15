"""A minimal in-memory adapter proving the contract suite is satisfiable.

This is test scaffolding only — it demonstrates the smallest correct
implementation of the SourceAdapter contract and gives the contract suite a
reference subject. Real adapters live under ``app/adapters/``.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from app.domain.ingestion.adapter import SourceAdapter
from app.domain.ingestion.contracts import (
    AdapterConfig,
    AdapterIdentity,
    CanonicalRecords,
    ConnectionStatus,
    CounterpartyData,
    EntityType,
    ExtractionResult,
    GlAccountData,
    HealthStatus,
    MappingConfig,
    PositionData,
    ProductData,
    RawRecord,
    SourceColumn,
    SourceSchema,
    SourceTable,
    TranslationFailureData,
)

VALID_LOCATION = "memory://fixture"

_DATA_MODELS = {
    "gl_account": GlAccountData,
    "counterparty": CounterpartyData,
    "product": ProductData,
    "position": PositionData,
}
_DECIMAL_FIELDS = {"balance", "notional", "interest_rate", "rate_spread"}


class InMemoryAdapter(SourceAdapter):
    """Serves rows from a dict of tables: ``{table_name: [row, ...]}``.

    ``AdapterConfig.options["entity_tables"]`` routes entity types to tables,
    mirroring how file adapters learn which sheet holds which entity.
    """

    def __init__(self, tables: dict[str, list[dict[str, Any]]]) -> None:
        self._tables = tables

    def identify(self) -> AdapterIdentity:
        return AdapterIdentity(name="in_memory", version="1.0", source_system="MANUAL")

    def validate_connection(self, config: AdapterConfig) -> ConnectionStatus:
        if config.location != VALID_LOCATION:
            return ConnectionStatus(ok=False, detail=f"Unknown location {config.location!r}.")
        if not self._tables:
            return ConnectionStatus(ok=False, detail="No tables loaded.")
        return ConnectionStatus(ok=True)

    def discover_schema(self, config: AdapterConfig) -> SourceSchema:
        tables = tuple(
            SourceTable(
                name=name,
                columns=tuple(SourceColumn(name=column) for column in (rows[0] if rows else {})),
                row_count=len(rows),
            )
            for name, rows in self._tables.items()
        )
        return SourceSchema(tables=tables)

    def extract(
        self,
        config: AdapterConfig,
        as_of_date: date,
        entity_types: list[EntityType],
    ) -> ExtractionResult:
        entity_tables: dict[str, str] = config.options.get("entity_tables", {})
        records: list[RawRecord] = []
        for entity_type in entity_types:
            table_name = entity_tables.get(entity_type)
            if table_name is None or table_name not in self._tables:
                continue
            for row_number, row in enumerate(self._tables[table_name], start=1):
                records.append(
                    RawRecord(
                        entity_type=entity_type,
                        source_locator=f"{config.location}#{table_name}!R{row_number}",
                        data=row,
                    )
                )
        digest = hashlib.sha256(
            json.dumps(self._tables, sort_keys=True, default=str).encode()
        ).hexdigest()
        return ExtractionResult(
            identity=self.identify(),
            as_of_date=as_of_date,
            extraction_mode="full",
            content_hash=digest,
            records=records,
        )

    def translate(
        self,
        raw_records: ExtractionResult,
        mapping_config: MappingConfig,
    ) -> CanonicalRecords:
        result = CanonicalRecords()
        buckets = {
            "gl_account": result.gl_accounts,
            "counterparty": result.counterparties,
            "product": result.products,
            "position": result.positions,
        }
        for record in raw_records.records:
            if record.entity_type == "reference":
                continue  # this reference subject only serves entity tables
            mapping = mapping_config.field_mappings.get(record.entity_type)
            if mapping is None:
                result.failures.append(
                    TranslationFailureData(
                        entity_type=record.entity_type,
                        source_locator=record.source_locator,
                        raw_record=record.data,
                        error_code="no_entity_mapping",
                        error_message=f"No mapping for entity type {record.entity_type!r}.",
                    )
                )
                continue
            values: dict[str, Any] = {"source_locator": record.source_locator}
            for canonical_field, source_columns in mapping.fields.items():
                if isinstance(source_columns, str):
                    value = record.data.get(source_columns)
                else:
                    value = next(
                        (record.data[column] for column in source_columns if column in record.data),
                        None,
                    )
                enum_map = mapping_config.enum_mappings.get(canonical_field)
                if enum_map is not None and value is not None:
                    value = enum_map.get(str(value), value)
                if canonical_field in _DECIMAL_FIELDS and value is not None:
                    with contextlib.suppress(InvalidOperation):
                        value = Decimal(str(value))
                values[canonical_field] = value
            try:
                model = _DATA_MODELS[record.entity_type]
                buckets[record.entity_type].append(model.model_validate(values))
            except Exception as exc:  # noqa: BLE001 - bad data must never abort the batch
                result.failures.append(
                    TranslationFailureData(
                        entity_type=record.entity_type,
                        source_locator=record.source_locator,
                        raw_record=record.data,
                        error_code="translation_error",
                        error_message=str(exc),
                    )
                )
        return result

    def health_check(self) -> HealthStatus:
        return HealthStatus(healthy=True)
