"""The Excel/CSV source adapter — AequorOS's first-class onboarding path.

Excel is not a fallback: it is how every mid-tier bank can deliver data on
day one, whatever their core system. The adapter treats a workbook as a real
source system: schema discovery over its sheets, deterministic extraction
with cell-level locators, and mapping-driven translation whose failures
preserve the raw row for onboarding refinement.
"""

from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.adapters.excel_csv.sheet_analyzer import AnalyzedTable, analyze_sheet
from app.adapters.excel_csv.type_coercion import (
    CoercionError,
    coerce_date,
    coerce_int,
    coerce_money,
    coerce_rate,
    coerce_string,
)
from app.adapters.excel_csv.workbook_reader import WorkbookReadError, read_source
from app.domain.ingestion.adapter import SourceAdapter, register_adapter
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

ADAPTER_NAME = "excel_csv"
ADAPTER_VERSION = "1.0"

_MONEY_FIELDS = frozenset({"balance", "notional"})
_RATE_FIELDS = frozenset({"interest_rate", "rate_spread"})
_DATE_FIELDS = frozenset({"origination_date", "contractual_maturity", "next_repricing_date"})
_INT_FIELDS = frozenset({"ifrs9_stage", "behavioral_maturity_months"})

_DATA_MODELS: dict[EntityType, type] = {
    "gl_account": GlAccountData,
    "counterparty": CounterpartyData,
    "product": ProductData,
    "position": PositionData,
}


class ExcelCsvAdapter(SourceAdapter):
    def identify(self) -> AdapterIdentity:
        return AdapterIdentity(
            name=ADAPTER_NAME, version=ADAPTER_VERSION, source_system="EXCEL_CSV"
        )

    def validate_connection(self, config: AdapterConfig) -> ConnectionStatus:
        try:
            sheets = read_source(Path(config.location))
        except WorkbookReadError as exc:
            return ConnectionStatus(ok=False, detail=str(exc))
        if not any(sheet.grid for sheet in sheets):
            return ConnectionStatus(ok=False, detail="Source contains no data.")
        return ConnectionStatus(ok=True)

    def discover_schema(self, config: AdapterConfig) -> SourceSchema:
        tables: list[SourceTable] = []
        for analyzed in self._analyzed_tables(config):
            columns = tuple(
                SourceColumn(
                    name=column,
                    sample_values=tuple(
                        str(row[column])
                        for _, row in analyzed.rows[:3]
                        if row.get(column) is not None
                    ),
                )
                for column in analyzed.columns
            )
            tables.append(
                SourceTable(name=analyzed.name, columns=columns, row_count=len(analyzed.rows))
            )
        return SourceSchema(tables=tuple(tables))

    def extract(
        self,
        config: AdapterConfig,
        as_of_date: date,
        entity_types: list[EntityType],
    ) -> ExtractionResult:
        path = Path(config.location)
        analyzed = {table.name: table for table in self._analyzed_tables(config)}
        entity_tables: dict[str, str] = config.options.get("entity_tables", {})

        records: list[RawRecord] = []
        warnings: list[str] = []
        for entity_type in entity_types:
            table_name = entity_tables.get(entity_type)
            if table_name is None:
                warnings.append(f"No table configured for entity type {entity_type!r}.")
                continue
            table = analyzed.get(table_name)
            if table is None:
                warnings.append(
                    f"Configured table {table_name!r} for {entity_type!r} was not found; "
                    f"available: {sorted(analyzed)}."
                )
                continue
            for row_number, row in table.rows:
                records.append(
                    RawRecord(
                        entity_type=entity_type,
                        source_locator=f"{path.name}#{table.name}!R{row_number}",
                        data=row,
                    )
                )

        return ExtractionResult(
            identity=self.identify(),
            as_of_date=as_of_date,
            extraction_mode="full",
            content_hash=hashlib.sha256(path.read_bytes()).hexdigest(),
            records=records,
            warnings=warnings,
        )

    def translate(
        self,
        raw_records: ExtractionResult,
        mapping_config: MappingConfig,
    ) -> CanonicalRecords:
        result = CanonicalRecords()
        buckets: dict[EntityType, list[Any]] = {
            "gl_account": result.gl_accounts,
            "counterparty": result.counterparties,
            "product": result.products,
            "position": result.positions,
        }
        dayfirst = bool(mapping_config.options.get("dayfirst", True))

        for record in raw_records.records:
            mapping = mapping_config.field_mappings.get(record.entity_type)
            if mapping is None:
                result.failures.append(
                    _failure(record, "no_entity_mapping", "No mapping for this entity type.")
                )
                continue

            values: dict[str, Any] = {"source_locator": record.source_locator}
            field_errors: list[str] = []
            for canonical_field, source_column in mapping.fields.items():
                raw_value = record.data.get(source_column)
                enum_map = mapping_config.enum_mappings.get(canonical_field)
                if enum_map is not None and raw_value is not None:
                    raw_value = enum_map.get(str(raw_value).strip(), raw_value)
                try:
                    values[canonical_field] = _coerce_field(
                        canonical_field, raw_value, dayfirst=dayfirst
                    )
                except CoercionError as exc:
                    field_errors.append(f"{canonical_field} (column {source_column!r}): {exc}")
            if field_errors:
                result.failures.append(_failure(record, "coercion_error", "; ".join(field_errors)))
                continue

            if record.entity_type == "product" and values.get("regulatory_category") is None:
                product_code = values.get("product_code")
                if product_code is not None:
                    values["regulatory_category"] = mapping_config.product_mappings.get(
                        str(product_code)
                    )

            try:
                model = _DATA_MODELS[record.entity_type]
                buckets[record.entity_type].append(model.model_validate(values))
            except ValidationError as exc:
                messages = "; ".join(
                    f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
                    for error in exc.errors()
                )
                result.failures.append(_failure(record, "invalid_record", messages))
        return result

    def health_check(self) -> HealthStatus:
        return HealthStatus(healthy=True)

    def _analyzed_tables(self, config: AdapterConfig) -> list[AnalyzedTable]:
        tables: list[AnalyzedTable] = []
        for sheet in read_source(Path(config.location)):
            tables.extend(analyze_sheet(sheet.name, sheet.grid, sheet.merged_ranges))
        return tables


def _coerce_field(canonical_field: str, value: Any, *, dayfirst: bool) -> Any:
    if canonical_field in _MONEY_FIELDS:
        return coerce_money(value)
    if canonical_field in _RATE_FIELDS:
        return coerce_rate(value)
    if canonical_field in _DATE_FIELDS:
        return coerce_date(value, dayfirst=dayfirst)
    if canonical_field in _INT_FIELDS:
        return coerce_int(value)
    return coerce_string(value)


def _failure(record: RawRecord, code: str, message: str) -> TranslationFailureData:
    return TranslationFailureData(
        entity_type=record.entity_type,
        source_locator=record.source_locator,
        raw_record=record.data,
        error_code=code,
        error_message=message,
    )


register_adapter("EXCEL_CSV", ExcelCsvAdapter)
