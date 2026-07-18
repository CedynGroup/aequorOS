"""Generic canonical translation for database-direct extract output.

A thin, backend-agnostic mapping layer identical in shape to the Temenos and
API-push translators: it renames source columns to canonical fields via the
:class:`MappingConfig`, applies ``enum_mappings``, coerces types, copies
``attribute_columns`` into the ``attributes`` payload, and derives a product's
regulatory category from ``product_mappings``. It knows nothing about Oracle,
SQL Server, JDBC, or ODBC — every backend-specific concern (charset, timezone,
locale numbers, native types) was already resolved during normalization, so this
stage is fully reproducible from the recorded mapping version.

Values arriving here are JSON-native (the normalization layer stringified dates,
decimals, and non-UTF text), so coercion is strict-by-contract with no
spreadsheet heuristics. Untranslatable records land in
``CanonicalRecords.failures`` with the raw record preserved — translation never
raises for bad data.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, ValidationError

from app.domain.ingestion.contracts import (
    CanonicalRecords,
    CounterpartyData,
    EntityType,
    ExtractionResult,
    GlAccountData,
    MappingConfig,
    PositionData,
    ProductData,
    RawRecord,
    ReferenceRowData,
    TranslationFailureData,
)

_MONEY_FIELDS = frozenset({"balance", "notional"})
_RATE_FIELDS = frozenset({"interest_rate", "rate_spread"})
_DATE_FIELDS = frozenset({"origination_date", "contractual_maturity", "next_repricing_date"})
_INT_FIELDS = frozenset({"ifrs9_stage", "behavioral_maturity_months"})
_DICT_FIELDS = ("attributes", "external_identifiers")

_DATA_MODELS: dict[EntityType, type[BaseModel]] = {
    "gl_account": GlAccountData,
    "counterparty": CounterpartyData,
    "product": ProductData,
    "position": PositionData,
}


class _CoercionError(ValueError):
    pass


def translate(  # noqa: PLR0912 - one branch per translation concern, mirrors api_push
    raw_records: ExtractionResult,
    mapping_config: MappingConfig,
) -> CanonicalRecords:
    """Translate database-direct extract output into canonical record data."""
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
            _translate_reference(record, reference_fields, reference_indexes, result)
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
                values[canonical_field] = _coerce_field(canonical_field, raw_value)
            except _CoercionError as exc:
                field_errors.append(f"{canonical_field} (column {source_column!r}): {exc}")
        if field_errors:
            result.failures.append(_failure(record, "coercion_error", "; ".join(field_errors)))
            continue

        model = _DATA_MODELS[record.entity_type]
        for dict_field in _DICT_FIELDS:
            payload = record.data.get(dict_field)
            if dict_field in model.model_fields and isinstance(payload, dict):
                values[dict_field] = payload
        attribute_extras = {
            column: _stringify(record.data[column])
            for column in mapping.attribute_columns
            if column in record.data and record.data[column] is not None
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


def _resolve_source_value(row: dict[str, Any], source_columns: str | list[str]) -> tuple[str, Any]:
    """The (column, value) a mapping entry reads from this record."""
    if isinstance(source_columns, str):
        return source_columns, row.get(source_columns)
    for column in source_columns:
        if column in row:
            return column, row[column]
    return source_columns[0] if source_columns else "", None


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
    raise _CoercionError(f"unsupported value type {type(value).__name__}")


def _coerce_decimal(value: Any) -> Decimal:
    if isinstance(value, bool | dict | list):
        raise _CoercionError(f"cannot read {value!r} as a number")
    try:
        return Decimal(str(value).strip())
    except InvalidOperation as exc:
        raise _CoercionError(f"cannot read {value!r} as a number") from exc


def _coerce_date(value: Any) -> date:
    """Accept ISO ``YYYY-MM-DD`` (and full ISO datetimes, date part) plus the
    packed ``YYYYMMDD`` form some cores store as text."""
    if isinstance(value, str):
        text = value.strip()
        try:
            return date.fromisoformat(text[:10]) if len(text) >= 10 else date.fromisoformat(text)  # noqa: PLR2004
        except ValueError:
            if len(text) == 8 and text.isdigit():  # noqa: PLR2004 - packed YYYYMMDD width
                try:
                    return date(int(text[:4]), int(text[4:6]), int(text[6:8]))
                except ValueError as exc:
                    raise _CoercionError(f"cannot read {value!r} as a date") from exc
    raise _CoercionError(f"cannot read {value!r} as a date (expected 'YYYY-MM-DD' or 'YYYYMMDD')")


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
