"""Deterministic sanctioned normalizer stages (NORMALIZE operation type).

Four rule-based cleaners, each a :class:`~app.etl.contracts.Preprocessor`:

* :class:`TextNormalizer` — Unicode NFC + whitespace (trim + internal-run collapse) on
  free-text string fields. The general "case+whitespace+unicode" cleaner; code-field case
  normalization is handled by the ISO stages below (which upper-case to the canonical form).
* :class:`CurrencyNormalizer` — ISO 4217: canonicalise a currency column to its alpha code.
* :class:`CountryNormalizer` — ISO 3166-1: canonicalise a country column to alpha-2.
* :class:`DateNormalizer` — ISO 8601: canonicalise string dates in date-typed columns to
  ``YYYY-MM-DD`` (Excel serials are handled by :mod:`..type_coercion`).

None of these ever *decides* to modify a regulatory-critical value on its own: every
candidate rewrite is routed through :func:`app.etl.resolve.make_operation`, which downgrades
a value-changing edit on a critical concept to a FLAG. Because currency resolves to the
critical ``currency`` concept, an unrecognised currency string is flagged, not guessed.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from typing import TYPE_CHECKING

from app.etl.contracts import ETLOperationType, Preprocessor
from app.etl.deduplication._fields import record_id as _record_id
from app.etl.preprocessing.normalizers._iso import normalize_country, normalize_currency
from app.etl.resolve import make_operation, resolve_concept

if TYPE_CHECKING:
    from app.domain.ingestion.contracts import RawRecord
    from app.etl.contracts import ETLOperation

# Null sentinels are the type-coercer's job; string values that are effectively empty are
# skipped here so a normalizer never manufactures an op out of a placeholder.
_EMPTY_SENTINELS: frozenset[str] = frozenset(
    {"", "n/a", "na", "n.a.", "-", "--", "tbc", "tbd", "none", "null", "nil", "#n/a"}
)


def _is_blank(value: object) -> bool:
    return value is None or str(value).strip().lower() in _EMPTY_SENTINELS


class TextNormalizer(Preprocessor):
    """Unicode NFC + whitespace normalization on free-text string fields."""

    operation_type = ETLOperationType.NORMALIZE
    _operation_ref = "text_normalizer/v1"
    # Concepts owned by a more specific normalizer/coercer; skipped here.
    _reserved_concepts: frozenset[str] = frozenset({"currency", "country"})

    def apply(self, record: RawRecord) -> list[ETLOperation]:
        rid = _record_id(record)
        ops: list[ETLOperation] = []
        for source_field, value in record.data.items():
            if not isinstance(value, str) or _is_blank(value):
                continue
            if resolve_concept(source_field) in self._reserved_concepts:
                continue
            if _is_date_field(source_field):
                continue  # dates are canonicalised by DateNormalizer / the coercer
            after = _collapse_ws(unicodedata.normalize("NFC", value))
            op = make_operation(
                record_id=rid,
                source_field=source_field,
                before=value,
                after=after,
                operation_type=ETLOperationType.NORMALIZE,
                operation_ref=self._operation_ref,
                value_preserving=True,
            )
            if op is not None:
                ops.append(op)
        return ops


class CurrencyNormalizer(Preprocessor):
    """Canonicalise currency columns to their ISO 4217 alpha code (or FLAG if unknown)."""

    operation_type = ETLOperationType.NORMALIZE
    _operation_ref = "iso4217_normalizer/v1"

    def apply(self, record: RawRecord) -> list[ETLOperation]:
        rid = _record_id(record)
        ops: list[ETLOperation] = []
        for source_field, value in record.data.items():
            if resolve_concept(source_field) != "currency":
                continue
            if _is_blank(value):
                continue
            raw = str(value)
            iso = normalize_currency(raw)
            if iso is None:
                # currency is regulatory-critical; an unrecognised code is a value-change
                # (to an unknown target) => make_operation flags it rather than guessing.
                op = make_operation(
                    record_id=rid,
                    source_field=source_field,
                    before=value,
                    after="__unresolved__",
                    operation_type=ETLOperationType.NORMALIZE,
                    operation_ref=self._operation_ref,
                    value_preserving=False,
                    flag_reason=(
                        f"currency {raw!r} is not a recognised ISO 4217 code; "
                        f"review before canonicalisation (regulatory-critical)."
                    ),
                )
            else:
                op = make_operation(
                    record_id=rid,
                    source_field=source_field,
                    before=value,
                    after=iso,
                    operation_type=ETLOperationType.NORMALIZE,
                    operation_ref=self._operation_ref,
                    value_preserving=True,
                )
            if op is not None:
                ops.append(op)
        return ops


class CountryNormalizer(Preprocessor):
    """Canonicalise country columns to ISO 3166-1 alpha-2 (non-critical -> SANCTIONED)."""

    operation_type = ETLOperationType.NORMALIZE
    _operation_ref = "iso3166_normalizer/v1"

    def apply(self, record: RawRecord) -> list[ETLOperation]:
        rid = _record_id(record)
        ops: list[ETLOperation] = []
        for source_field, value in record.data.items():
            if resolve_concept(source_field) != "country":
                continue
            if _is_blank(value):
                continue
            iso = normalize_country(str(value))
            if iso is None:
                continue  # country is not regulatory-critical; leave an unknown value as-is
            op = make_operation(
                record_id=rid,
                source_field=source_field,
                before=value,
                after=iso,
                operation_type=ETLOperationType.NORMALIZE,
                operation_ref=self._operation_ref,
                value_preserving=True,
            )
            if op is not None:
                ops.append(op)
        return ops


# String date formats accepted by the ISO 8601 normalizer. Day-first is tried before
# month-first because the operating region writes DD/MM/YYYY; an unambiguous day (>12)
# is resolved correctly by either ordering.
_DATE_FORMATS: tuple[str, ...] = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%d.%m.%Y",
    "%m/%d/%Y",
    "%d %b %Y",
    "%d %B %Y",
    "%d-%b-%Y",
    "%d-%b-%y",
    "%b %d, %Y",
    "%B %d, %Y",
)

_DATE_FIELD_TOKENS: tuple[str, ...] = (
    "date",
    "maturity",
    "repricing",
    "origination",
    "expiry",
    "valuedt",
    "asof",
)


def _is_date_field(source_field: str) -> bool:
    key = re.sub(r"[^a-z0-9]", "", source_field.lower())
    return any(token in key for token in _DATE_FIELD_TOKENS)


def parse_date_string(raw: str) -> str | None:
    """Parse a string date in a known format to an ISO ``YYYY-MM-DD`` string, else None."""
    text = raw.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date().isoformat()  # noqa: DTZ007 - date-only
        except ValueError:
            continue
    return None


class DateNormalizer(Preprocessor):
    """Canonicalise string dates in date-typed columns to ISO 8601 ``YYYY-MM-DD``."""

    operation_type = ETLOperationType.NORMALIZE
    _operation_ref = "iso8601_normalizer/v1"

    def apply(self, record: RawRecord) -> list[ETLOperation]:
        rid = _record_id(record)
        ops: list[ETLOperation] = []
        for source_field, value in record.data.items():
            if not _is_date_field(source_field) or _is_blank(value):
                continue
            if not isinstance(value, str):
                continue  # numeric Excel serials are handled by the type coercer
            iso = parse_date_string(value)
            if iso is None:
                continue
            op = make_operation(
                record_id=rid,
                source_field=source_field,
                before=value,
                after=iso,
                operation_type=ETLOperationType.NORMALIZE,
                operation_ref=self._operation_ref,
                value_preserving=True,
            )
            if op is not None:
                ops.append(op)
        return ops


def _collapse_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()
