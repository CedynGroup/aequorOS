"""The Excel/CSV source adapter — AequorOS's first-class onboarding path.

Excel is not a fallback: it is how every mid-tier bank can deliver data on
day one, whatever their core system. The adapter treats a workbook as a real
source system: schema discovery over its sheets, deterministic extraction
with cell-level locators, and mapping-driven translation whose failures
preserve the raw row for onboarding refinement.
"""

from __future__ import annotations

import difflib
import hashlib
import re
from datetime import date, datetime
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
    is_null_like,
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
    ReferenceRowData,
    SourceColumn,
    SourceSchema,
    SourceTable,
    SourceTableSummary,
    TranslationFailureData,
    UnmatchedMapping,
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

_NORMALIZE = re.compile(r"[^a-z0-9]+")


def _normalized(name: str) -> str:
    """Table-name normal form: lowercase, non-alphanumerics stripped."""
    return _NORMALIZE.sub("", name.lower())


class _TableResolver:
    """Resolves configured table names against the tables actually present.

    Precedence per candidate name: exact match, then case-insensitive, then
    normalized (strip non-alphanumerics, lowercase). Every candidate is
    resolved independently; distinct matches are returned in candidate order.
    """

    def __init__(self, tables: dict[str, AnalyzedTable]) -> None:
        self._exact = tables
        self._ci: dict[str, AnalyzedTable] = {}
        self._normalized: dict[str, AnalyzedTable] = {}
        for name, table in tables.items():
            self._ci.setdefault(name.casefold(), table)
            self._normalized.setdefault(_normalized(name), table)

    def resolve_one(self, candidate: str) -> AnalyzedTable | None:
        return (
            self._exact.get(candidate)
            or self._ci.get(candidate.casefold())
            or self._normalized.get(_normalized(candidate))
        )

    def resolve(self, candidates: list[str]) -> list[AnalyzedTable]:
        matches: list[AnalyzedTable] = []
        seen: set[str] = set()
        for candidate in candidates:
            table = self.resolve_one(candidate)
            if table is not None and table.name not in seen:
                seen.add(table.name)
                matches.append(table)
        return matches

    def closest(self, candidates: list[str]) -> str | None:
        """The present table whose normalized name is nearest any candidate."""
        by_normalized = {_normalized(name): name for name in self._exact}
        for candidate in candidates:
            close = difflib.get_close_matches(
                _normalized(candidate), list(by_normalized), n=1, cutoff=0.6
            )
            if close:
                return by_normalized[close[0]]
        return None


def _candidate_names(value: Any) -> list[str]:
    """Accepts the legacy single-table option value or a candidate list."""
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value]


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
        resolver = _TableResolver(analyzed)
        entity_tables: dict[str, Any] = config.options.get("entity_tables", {})
        reference_tables: dict[str, Any] = config.options.get("reference_tables", {})

        records: list[RawRecord] = []
        warnings: list[str] = []
        unmatched: list[UnmatchedMapping] = []

        def extract_tables(
            kind: str,
            candidates: list[str],
            entity_type: EntityType | None,
            dataset_kind: str | None,
        ) -> None:
            tables = resolver.resolve(candidates)
            if not tables:
                suggestion = resolver.closest(candidates)
                unmatched.append(
                    UnmatchedMapping(
                        mapping=kind, expected=tuple(candidates), suggestion=suggestion
                    )
                )
                hint = f" Closest present table: {suggestion!r}." if suggestion else ""
                warnings.append(
                    f"No table matching {candidates} for {kind!r} was found; "
                    f"available: {sorted(analyzed)}.{hint}"
                )
                return
            for table in tables:
                for row_number, row in table.rows:
                    records.append(
                        RawRecord(
                            entity_type=entity_type if entity_type is not None else "reference",
                            source_locator=f"{path.name}#{table.name}!R{row_number}",
                            data=row,
                            dataset_kind=dataset_kind,
                        )
                    )

        for entity_type in entity_types:
            configured = entity_tables.get(entity_type)
            if configured is None:
                warnings.append(f"No table configured for entity type {entity_type!r}.")
                continue
            extract_tables(entity_type, _candidate_names(configured), entity_type, None)

        for name, reference in reference_tables.items():
            extract_tables(
                f"reference:{name}",
                _candidate_names(reference.get("tables", [])),
                None,
                str(reference.get("dataset_kind", "")) or None,
            )

        return ExtractionResult(
            identity=self.identify(),
            as_of_date=as_of_date,
            extraction_mode="full",
            content_hash=hashlib.sha256(path.read_bytes()).hexdigest(),
            records=records,
            warnings=warnings,
            source_tables=[
                SourceTableSummary(name=table.name, row_count=len(table.rows))
                for table in analyzed.values()
            ],
            unmatched_mappings=unmatched,
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
        reference_fields = {
            mapping.dataset_kind: mapping.fields
            for mapping in reversed(list(mapping_config.reference_mappings.values()))
        }
        reference_indexes: dict[str, int] = {}

        for record in raw_records.records:
            if record.entity_type == "reference":
                self._translate_reference(record, reference_fields, reference_indexes, result)
                continue

            mapping = mapping_config.field_mappings.get(record.entity_type)
            if mapping is None:
                result.failures.append(
                    _failure(record, "no_entity_mapping", "No mapping for this entity type.")
                )
                continue

            values: dict[str, Any] = {"source_locator": record.source_locator}
            field_errors: list[str] = []
            for canonical_field, source_columns in mapping.fields.items():
                source_column, raw_value = _resolve_source_value(record.data, source_columns)
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

            attributes = {
                column: _stringify(record.data[column])
                for column in mapping.attribute_columns
                if column in record.data and not is_null_like(record.data[column])
            }
            if attributes:
                values["attributes"] = attributes

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

    def _translate_reference(
        self,
        record: RawRecord,
        reference_fields: dict[str, list[str]],
        reference_indexes: dict[str, int],
        result: CanonicalRecords,
    ) -> None:
        """Preserve a reference row as a stringified payload under its kind."""
        kind = record.dataset_kind
        if kind is None or kind not in reference_fields:
            result.failures.append(
                _failure(record, "no_reference_mapping", "No reference mapping for this table.")
            )
            return
        selected = reference_fields[kind]
        payload = {
            column: _stringify(value)
            for column, value in record.data.items()
            if not selected or column in selected
        }
        reference_indexes[kind] = reference_indexes.get(kind, 0) + 1
        try:
            result.reference_rows.append(
                ReferenceRowData(
                    dataset_kind=kind,  # type: ignore[arg-type]
                    source_locator=record.source_locator,
                    row_index=reference_indexes[kind],
                    payload=payload,
                )
            )
        except ValidationError as exc:
            result.failures.append(_failure(record, "invalid_reference_row", str(exc)))

    def health_check(self) -> HealthStatus:
        return HealthStatus(healthy=True)

    def _analyzed_tables(self, config: AdapterConfig) -> list[AnalyzedTable]:
        tables: list[AnalyzedTable] = []
        for sheet in read_source(Path(config.location)):
            tables.extend(analyze_sheet(sheet.name, sheet.grid, sheet.merged_ranges))
        return tables


def _resolve_source_value(row: dict[str, Any], source_columns: str | list[str]) -> tuple[str, Any]:
    """The (column, value) a mapping entry reads from this row.

    A list means fallback columns: the first column present in the row wins,
    even when its cell is empty — presence, not non-emptiness, keeps the
    resolution deterministic per sheet layout.
    """
    if isinstance(source_columns, str):
        return source_columns, row.get(source_columns)
    for column in source_columns:
        if column in row:
            return column, row[column]
    return source_columns[0] if source_columns else "", None


def _stringify(value: Any) -> str | None:
    """Stringify a payload value: dates ISO, numbers verbatim, nulls preserved."""
    if is_null_like(value):
        return None
    if isinstance(value, datetime):
        # Excel stores pure dates as midnight datetimes; keep them as dates.
        pure_date = value.time() == datetime.min.time()
        return value.date().isoformat() if pure_date else value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value.strip() if isinstance(value, str) else str(value)


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
