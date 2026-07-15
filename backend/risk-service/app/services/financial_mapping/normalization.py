from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation

from app.schemas.common import JsonObject
from app.services.financial_mapping.types import (
    DecimalField,
    FieldAlias,
    FieldValue,
)


def normalize_row(payload: JsonObject) -> dict[str, FieldValue]:
    normalized: dict[str, FieldValue] = {}
    for source_field, value in payload.items():
        normalized_key = normalize_key(source_field)
        normalized.setdefault(
            normalized_key,
            FieldValue(canonical_name=normalized_key, source_field=source_field, value=value),
        )
    return normalized


def first_field(
    normalized: dict[str, FieldValue],
    aliases: tuple[FieldAlias, ...],
) -> FieldValue | None:
    for alias in aliases:
        field = normalized.get(alias)
        if field is not None and field.value not in (None, ""):
            return FieldValue(
                canonical_name=alias,
                source_field=field.source_field,
                value=field.value,
            )
    return None


def first_decimal(
    normalized: dict[str, FieldValue],
    aliases: tuple[FieldAlias, ...],
) -> DecimalField | None:
    field = first_field(normalized, aliases)
    if field is None:
        return None
    value = parse_decimal(field.value)
    if value is None:
        return None
    return DecimalField(source_field=field.source_field, value=value)


def normalize_key(value: str) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^0-9a-zA-Z]+", "_", value.strip().lower())).strip("_")


def normalize_text(value: object) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value).strip()).lower()


def string_value(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def parse_decimal(value: object) -> Decimal | None:  # noqa: PLR0911
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value))
    if not isinstance(value, str):
        return None

    text = value.strip()
    if not text:
        return None
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()")
    text = re.sub(r"[^0-9.\-]", "", text)
    if text in {"", "-", ".", "-."}:
        return None
    try:
        parsed = Decimal(text)
    except InvalidOperation:
        return None
    return -parsed if negative else parsed


def parse_date(value: object) -> date | None:
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def normalize_currency(value: object) -> str | None:
    if value is None:
        return None
    currency = str(value).strip().upper()
    return currency if len(currency) == 3 and currency.isalpha() else None
