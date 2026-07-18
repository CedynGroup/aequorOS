"""Deterministic type coercion (TYPE_COERCE operation type).

One :class:`~app.etl.contracts.Preprocessor` that turns spreadsheet-shaped strings into
canonical scalar forms:

* percent strings — ``"15.5%"`` -> ``"0.155"`` (value-preserving: the parsed rate is equal);
* thousands separators — ``"1,234.56"`` -> ``"1234.56"`` (value-preserving);
* null sentinels — ``"N/A"`` / ``"-"`` / ``"TBC"`` / ``""`` -> ``None`` (value-*changing*);
* Excel serial dates — ``46142`` in a date column -> ``"2026-04-30"``.

The §12.5 discipline is enforced centrally by :func:`app.etl.resolve.make_operation`. A
value-preserving coercion (percent, thousands) on a regulatory-critical concept reached
through a raw alias (``balance_ghs``, ``interest_rate_pct``) is SANCTIONED; nulling a
regulatory-critical value is a *change* and is therefore FLAGGED for a human — a missing
balance or rate is surfaced, never silently zeroed (the Sample Bank data deliberately
carries such gaps).
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING

from app.etl.contracts import ETLOperationType, Preprocessor
from app.etl.deduplication._fields import record_id as _record_id
from app.etl.preprocessing.normalizers.stages import _is_date_field
from app.etl.resolve import make_operation

if TYPE_CHECKING:
    from app.domain.ingestion.contracts import RawRecord
    from app.etl.contracts import ETLOperation

_OPERATION_REF = "type_coercion/v1"

# Excel's 1900 date system counts days from 1899-12-30 (the offset absorbs Excel's
# fictional 1900-02-29 leap day for all serials at or above 61 / 1900-03-01).
_EXCEL_EPOCH = date(1899, 12, 30)
# Plausible Excel-serial window for a bank as-of date: ~1980-01-01 .. ~2079-12-31. Numbers
# outside this range in a date column are left untouched rather than mis-coerced.
_EXCEL_SERIAL_MIN = 29221
_EXCEL_SERIAL_MAX = 65380

_NULL_SENTINELS: frozenset[str] = frozenset(
    {"", "n/a", "na", "n.a.", "-", "--", "tbc", "tbd", "none", "null", "nil", "#n/a", "."}
)

# A value that is a plain number optionally carrying thousands separators / a leading sign.
_THOUSANDS_RE = re.compile(r"^[+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?$")
_PERCENT_RE = re.compile(r"^[+-]?\d+(?:[.,]\d+)?\s*%$")


class TypeCoercer(Preprocessor):
    """Coerce spreadsheet string cells to canonical scalar forms."""

    operation_type = ETLOperationType.TYPE_COERCE

    def apply(self, record: RawRecord) -> list[ETLOperation]:
        rid = _record_id(record)
        ops: list[ETLOperation] = []
        for source_field, value in record.data.items():
            op = self._coerce_field(rid, source_field, value)
            if op is not None:
                ops.append(op)
        return ops

    def _coerce_field(
        self, rid: str, source_field: str, value: object
    ) -> ETLOperation | None:
        # (1) Excel serial date in a date column (numeric or numeric-string).
        if _is_date_field(source_field):
            iso = _excel_serial_to_iso(value)
            if iso is not None:
                return make_operation(
                    record_id=rid,
                    source_field=source_field,
                    before=value,
                    after=iso,
                    operation_type=ETLOperationType.TYPE_COERCE,
                    operation_ref=_OPERATION_REF,
                    value_preserving=True,
                )

        if not isinstance(value, str):
            return None
        text = value.strip()

        # (2) Null sentinel -> None (value-changing; FLAGGED on critical concepts).
        if text.lower() in _NULL_SENTINELS:
            return make_operation(
                record_id=rid,
                source_field=source_field,
                before=value,
                after=None,
                operation_type=ETLOperationType.TYPE_COERCE,
                operation_ref=_OPERATION_REF,
                value_preserving=False,
                flag_reason=(
                    f"{source_field!r} carries the missing-value sentinel {value!r}; a missing "
                    f"regulatory-critical value must be reviewed, not coerced to null."
                ),
            )

        # (3) Percent string -> fractional decimal (value-preserving).
        if _PERCENT_RE.match(text):
            after = _percent_to_fraction(text)
            if after is not None:
                return make_operation(
                    record_id=rid,
                    source_field=source_field,
                    before=value,
                    after=after,
                    operation_type=ETLOperationType.TYPE_COERCE,
                    operation_ref=_OPERATION_REF,
                    value_preserving=True,
                )

        # (4) Thousands-separated number -> bare decimal string (value-preserving).
        if _THOUSANDS_RE.match(text):
            after = _strip_thousands(text)
            if after is not None:
                return make_operation(
                    record_id=rid,
                    source_field=source_field,
                    before=value,
                    after=after,
                    operation_type=ETLOperationType.TYPE_COERCE,
                    operation_ref=_OPERATION_REF,
                    value_preserving=True,
                )
        return None


def _percent_to_fraction(text: str) -> str | None:
    body = text.rstrip("%").strip().replace(",", ".")
    try:
        return _canonical_decimal(Decimal(body) / Decimal(100))
    except (InvalidOperation, ValueError):
        return None


def _strip_thousands(text: str) -> str | None:
    try:
        return _canonical_decimal(Decimal(text.replace(",", "")))
    except (InvalidOperation, ValueError):
        return None


def _canonical_decimal(value: Decimal) -> str:
    """Normalized decimal string without exponent notation or trailing-zero drift."""
    normalized = value.normalize()
    # ``normalize`` may render small/large magnitudes in scientific notation; expand it.
    text = format(normalized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _excel_serial_to_iso(value: object) -> str | None:
    serial: int | None = None
    if isinstance(value, bool):  # bools are ints in Python; never a date serial
        return None
    if isinstance(value, int):
        serial = value
    elif isinstance(value, float) and value.is_integer():
        serial = int(value)
    elif isinstance(value, str):
        text = value.strip()
        if re.fullmatch(r"\d+(?:\.0+)?", text):
            serial = int(float(text))
    if serial is None or not _EXCEL_SERIAL_MIN <= serial <= _EXCEL_SERIAL_MAX:
        return None
    return (_EXCEL_EPOCH + timedelta(days=serial)).isoformat()
