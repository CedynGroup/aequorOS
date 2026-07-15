"""The API push source adapter — programmatic ingestion for institutions.

Instead of dropping files, an institution's middleware POSTs JSON to the push
endpoints; the staged pages are assembled into one JSON document and that
document is this adapter's *source*. The shape is the public contract
(docs/API_INTEGRATION.md):

    {
      "as_of_date": "YYYY-MM-DD",
      "entities":  {"gl_account": [...], "counterparty": [...],
                    "product": [...], "position": [...]},
      "reference": {"yield_curve": [...], "capital_structure": [...], ...}
    }

Each ``entities``/``reference`` key is treated as a source *table*, so schema
discovery, mapping-driven table resolution, per-table extraction reporting,
and lineage locators (``source.json#position!R14``) work exactly like a
workbook's sheets. Field names default to the canonical contract fields
(identity mapping, zero onboarding config); a per-institution
``MappingConfig`` with ``source_system="API_PUSH"`` translates foreign field
names when the middleware cannot conform.

Values are JSON-native by contract: numbers (or plain numeric strings) for
amounts, decimal fractions for rates (0.245, never "24.5%"), ISO ``YYYY-MM-DD``
dates. Unlike the Excel adapter there is no spreadsheet-chaos tolerance —
ambiguous programmatic input is an error the middleware must fix.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from app.domain.ingestion.adapter import SourceAdapter, register_adapter
from app.domain.ingestion.constants import REFERENCE_DATASET_KINDS
from app.domain.ingestion.contracts import (
    AdapterConfig,
    AdapterIdentity,
    CanonicalRecords,
    ConnectionStatus,
    CounterpartyData,
    EntityMapping,
    EntityType,
    ExtractionResult,
    GlAccountData,
    HealthStatus,
    MappingConfig,
    PositionData,
    ProductData,
    RawRecord,
    ReferenceMapping,
    ReferenceRowData,
    SourceColumn,
    SourceSchema,
    SourceTable,
    SourceTableSummary,
    TranslationFailureData,
)

ADAPTER_NAME = "api_push"
ADAPTER_VERSION = "1.0"

_MONEY_FIELDS = frozenset({"balance", "notional"})
_RATE_FIELDS = frozenset({"interest_rate", "rate_spread"})
_DATE_FIELDS = frozenset({"origination_date", "contractual_maturity", "next_repricing_date"})
_INT_FIELDS = frozenset({"ifrs9_stage", "behavioral_maturity_months"})
# Dict-valued canonical fields pass through verbatim when present on a record
# and defined on the target model (attributes everywhere, external
# identifiers on counterparties).
_DICT_FIELDS = ("attributes", "external_identifiers")

_DATA_MODELS: dict[EntityType, type[BaseModel]] = {
    "gl_account": GlAccountData,
    "counterparty": CounterpartyData,
    "product": ProductData,
    "position": PositionData,
}


def identity_mapping_config() -> MappingConfig:
    """The zero-config mapping: payload field names ARE the canonical names.

    Derived from the canonical record contracts so the public API contract can
    never drift from what translation actually accepts. Dict-valued fields
    (``attributes``, ``external_identifiers``) pass through outside the field
    mapping, and every reference dataset kind maps to the payload key of the
    same name.
    """

    def fields_for(model: type[BaseModel]) -> dict[str, str | list[str]]:
        skip = {"source_locator", *_DICT_FIELDS}
        return {name: name for name in model.model_fields if name not in skip}

    return MappingConfig(
        field_mappings={
            entity_type: EntityMapping(source_table=entity_type, fields=fields_for(model))
            for entity_type, model in _DATA_MODELS.items()
        },
        reference_mappings={
            kind: ReferenceMapping(source_table=kind, dataset_kind=kind)  # type: ignore[arg-type]
            for kind in REFERENCE_DATASET_KINDS
        },
    )


class PushSourceError(ValueError):
    """The staged push document is unreadable or not envelope-shaped."""


class _CoercionError(ValueError):
    pass


def _load_tables(path: Path) -> dict[str, list[dict[str, Any]]]:
    """Read the staged document into ``{table name: rows}``.

    Entity keys and reference keys are disjoint namespaces by construction
    (the push endpoints validate them against the canonical literals), so one
    flat table map serves resolution, discovery, and reporting.
    """
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PushSourceError(f"Cannot read push document {path.name}: {exc}") from exc
    if not isinstance(document, dict):
        raise PushSourceError("Push document must be a JSON object.")

    tables: dict[str, list[dict[str, Any]]] = {}
    for section in ("entities", "reference"):
        payload = document.get(section, {})
        if not isinstance(payload, dict):
            raise PushSourceError(f"Push document {section!r} must be a JSON object.")
        for name, rows in payload.items():
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise PushSourceError(
                    f"Push document {section}.{name} must be a list of JSON objects."
                )
            tables[str(name)] = rows
    return tables


def _candidate_names(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value]


class ApiPushAdapter(SourceAdapter):
    def identify(self) -> AdapterIdentity:
        return AdapterIdentity(name=ADAPTER_NAME, version=ADAPTER_VERSION, source_system="API_PUSH")

    def validate_connection(self, config: AdapterConfig) -> ConnectionStatus:
        path = Path(config.location)
        if not path.is_file():
            return ConnectionStatus(ok=False, detail=f"Source {path} does not exist.")
        try:
            tables = _load_tables(path)
        except PushSourceError as exc:
            return ConnectionStatus(ok=False, detail=str(exc))
        if not any(tables.values()):
            return ConnectionStatus(ok=False, detail="Push document contains no records.")
        return ConnectionStatus(ok=True)

    def discover_schema(self, config: AdapterConfig) -> SourceSchema:
        tables: list[SourceTable] = []
        for name, rows in _load_tables(Path(config.location)).items():
            columns: dict[str, list[str]] = {}
            for row in rows:
                for column, value in row.items():
                    samples = columns.setdefault(column, [])
                    if value is not None and len(samples) < 3:
                        samples.append(str(value))
            tables.append(
                SourceTable(
                    name=name,
                    columns=tuple(
                        SourceColumn(name=column, sample_values=tuple(samples))
                        for column, samples in columns.items()
                    ),
                    row_count=len(rows),
                )
            )
        return SourceSchema(tables=tuple(tables))

    def extract(
        self,
        config: AdapterConfig,
        as_of_date: date,
        entity_types: list[EntityType],
    ) -> ExtractionResult:
        path = Path(config.location)
        tables = _load_tables(path)
        by_casefold = {name.casefold(): name for name in tables}
        entity_tables: dict[str, Any] = config.options.get("entity_tables", {})
        reference_tables: dict[str, Any] = config.options.get("reference_tables", {})

        records: list[RawRecord] = []
        warnings: list[str] = []

        def resolve(candidates: list[str]) -> list[str]:
            matches: list[str] = []
            for candidate in candidates:
                name = candidate if candidate in tables else by_casefold.get(candidate.casefold())
                if name is not None and name not in matches:
                    matches.append(name)
            return matches

        def extract_tables(
            candidates: list[str],
            entity_type: EntityType | None,
            dataset_kind: str | None,
        ) -> None:
            # A configured mapping whose key is absent is NOT unmatched: a
            # push contains whatever the client chose to send this time, so
            # absence carries no diagnostic weight (unlike a workbook, whose
            # mapping declares what the file should contain). Pushed keys
            # nothing consumed stay visible in the per-table breakdown
            # (resolved_to null), and a push where NOTHING resolves is still
            # rejected by the orchestrator's zero-extraction blocker.
            for name in resolve(candidates):
                for index, row in enumerate(tables[name], start=1):
                    records.append(
                        RawRecord(
                            entity_type=entity_type if entity_type is not None else "reference",
                            source_locator=f"{path.name}#{name}!R{index}",
                            data=row,
                            dataset_kind=dataset_kind,
                            source_table=name,
                        )
                    )

        for entity_type in entity_types:
            configured = entity_tables.get(entity_type)
            if configured is None:
                warnings.append(f"No payload key configured for entity type {entity_type!r}.")
                continue
            extract_tables(_candidate_names(configured), entity_type, None)

        for reference in reference_tables.values():
            extract_tables(
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
                SourceTableSummary(name=name, row_count=len(rows)) for name, rows in tables.items()
            ],
        )

    def translate(  # noqa: PLR0912 - one branch per translation concern, mirrors excel_csv
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
            for canonical_field, source_fields in mapping.fields.items():
                source_field, raw_value = _resolve_source_value(record.data, source_fields)
                enum_map = mapping_config.enum_mappings.get(canonical_field)
                if enum_map is not None and raw_value is not None:
                    raw_value = enum_map.get(str(raw_value).strip(), raw_value)
                try:
                    values[canonical_field] = _coerce_field(canonical_field, raw_value)
                except _CoercionError as exc:
                    field_errors.append(f"{canonical_field} (field {source_field!r}): {exc}")
            if field_errors:
                result.failures.append(_failure(record, "coercion_error", "; ".join(field_errors)))
                continue

            model = _DATA_MODELS[record.entity_type]
            for dict_field in _DICT_FIELDS:
                payload = record.data.get(dict_field)
                if dict_field in model.model_fields and isinstance(payload, dict):
                    values[dict_field] = payload
            attribute_extras = {
                field: _stringify(record.data[field])
                for field in mapping.attribute_columns
                if field in record.data and record.data[field] is not None
            }
            if attribute_extras:
                values["attributes"] = {**values.get("attributes", {}), **attribute_extras}

            if record.entity_type == "product" and values.get("regulatory_category") is None:
                product_code = values.get("product_code")
                if product_code is not None:
                    values["regulatory_category"] = mapping_config.product_mappings.get(
                        str(product_code)
                    )

            try:
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
                _failure(record, "no_reference_mapping", "No reference mapping for this key.")
            )
            return
        selected = reference_fields[kind]
        payload = {
            field: _stringify(value)
            for field, value in record.data.items()
            if not selected or field in selected
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


def _resolve_source_value(row: dict[str, Any], source_fields: str | list[str]) -> tuple[str, Any]:
    """The (field, value) a mapping entry reads from this record.

    A list means fallback fields: the first field present in the record wins,
    even when its value is null — presence keeps resolution deterministic.
    """
    if isinstance(source_fields, str):
        return source_fields, row.get(source_fields)
    for field in source_fields:
        if field in row:
            return field, row[field]
    return source_fields[0] if source_fields else "", None


def _is_missing(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _coerce_field(canonical_field: str, value: Any) -> Any:
    """JSON-native coercion: strict by contract, no spreadsheet heuristics."""
    if _is_missing(value):
        return None
    if canonical_field in _MONEY_FIELDS or canonical_field in _RATE_FIELDS:
        return _coerce_decimal(value)
    if canonical_field in _DATE_FIELDS:
        return _coerce_date(value)
    if canonical_field in _INT_FIELDS:
        return _coerce_int(value)
    return str(value).strip() if not isinstance(value, dict | list) else _fail_type(value)


def _fail_type(value: Any) -> Any:
    raise _CoercionError(f"unsupported JSON type {type(value).__name__}")


def _coerce_decimal(value: Any) -> Decimal:
    if isinstance(value, bool | dict | list):
        raise _CoercionError(f"cannot read {value!r} as a number")
    try:
        return Decimal(str(value).strip())
    except InvalidOperation as exc:
        raise _CoercionError(
            f"cannot read {value!r} as a number (send a JSON number or plain numeric string)"
        ) from exc


def _coerce_date(value: Any) -> date:
    if not isinstance(value, str):
        raise _CoercionError(f"cannot read {value!r} as a date (send an ISO 'YYYY-MM-DD' string)")
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise _CoercionError(
            f"cannot read {value!r} as a date (send an ISO 'YYYY-MM-DD' string)"
        ) from exc


def _coerce_int(value: Any) -> int:
    if isinstance(value, bool):
        raise _CoercionError("booleans are not integers")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError as exc:
            raise _CoercionError(f"cannot read {value!r} as an integer") from exc
    raise _CoercionError(f"cannot read {value!r} as an integer")


def _stringify(value: Any) -> str | None:
    if _is_missing(value):
        return None
    return value.strip() if isinstance(value, str) else str(value)


def _failure(record: RawRecord, code: str, message: str) -> TranslationFailureData:
    return TranslationFailureData(
        entity_type=record.entity_type,
        source_locator=record.source_locator,
        raw_record=record.data,
        error_code=code,
        error_message=message,
    )


register_adapter("API_PUSH", ApiPushAdapter)
