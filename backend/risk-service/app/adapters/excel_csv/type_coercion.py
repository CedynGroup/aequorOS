"""Robust type parsing for the messy values banks put in spreadsheets.

Every coercion either returns a typed value, returns ``None`` for the
recognized "no value" placeholders banks use, or raises :class:`CoercionError`
with a message precise enough to fix the source cell. Coercions never guess
silently: ambiguous input is an error, not a hunch.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation

NULL_PLACEHOLDERS = frozenset({"", "-", "--", "n/a", "na", "n.a.", "nil", "none", "tbc", "#n/a"})

# Excel serial day 1 is 1900-01-01, but Excel wrongly treats 1900 as a leap
# year, so the usable epoch is 1899-12-30. Serials in this window cover
# business dates 1954-2173 — anything else is more plausibly a number.
_EXCEL_EPOCH = date(1899, 12, 30)
_SERIAL_MIN, _SERIAL_MAX = 20_000, 100_000

_CURRENCY_NOISE = re.compile(r"[^\d.,()\-+]")
_ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_SLASHED_DATE = re.compile(r"^(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})$")


class CoercionError(ValueError):
    def __init__(self, kind: str, value: object, reason: str) -> None:
        self.kind = kind
        self.value = value
        super().__init__(f"Cannot read {value!r} as {kind}: {reason}")


def is_null_like(value: object) -> bool:
    return value is None or (isinstance(value, str) and value.strip().lower() in NULL_PLACEHOLDERS)


def coerce_string(value: object) -> str | None:
    if is_null_like(value):
        return None
    if isinstance(value, str):
        return value.strip()
    return str(value)


def coerce_money(value: object) -> Decimal | None:
    """Parse a monetary amount from spreadsheet chaos.

    Handles currency symbols and codes ("GHS 1,500,000.50"), thousand
    separators, surrounding whitespace, and accounting-style parentheses for
    negatives ("(1,234.56)").
    """
    if is_null_like(value):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int | float):
        return Decimal(str(value))
    if not isinstance(value, str):
        raise CoercionError("money", value, f"unsupported type {type(value).__name__}")

    text = value.strip()
    negative = text.startswith("(") and text.endswith(")")
    cleaned = _CURRENCY_NOISE.sub("", text).replace("(", "").replace(")", "")
    cleaned = cleaned.replace(",", "")
    if not cleaned or cleaned in {"-", "+"}:
        raise CoercionError("money", value, "no digits found")
    try:
        amount = Decimal(cleaned)
    except InvalidOperation as exc:
        raise CoercionError("money", value, "not a number after cleanup") from exc
    return -amount if negative and amount > 0 else amount


def coerce_rate(value: object) -> Decimal | None:
    """Normalize a rate to decimal form: 24.5, "24.5%", and 0.245 all → 0.245.

    Bare numbers above 1.5 are treated as percentages: no plausible interest
    rate is 150%+ expressed as a decimal, and Ghanaian lending rates in the
    20-40 range are routinely typed without a percent sign.
    """
    if is_null_like(value):
        return None
    percent_explicit = False
    if isinstance(value, str):
        text = value.strip()
        if text.endswith("%"):
            percent_explicit = True
            text = text[:-1].strip()
        try:
            number = Decimal(text.replace(",", ""))
        except InvalidOperation as exc:
            raise CoercionError("rate", value, "not a number") from exc
    elif isinstance(value, Decimal):
        number = value
    elif isinstance(value, int | float):
        number = Decimal(str(value))
    else:
        raise CoercionError("rate", value, f"unsupported type {type(value).__name__}")

    if percent_explicit or number > Decimal("1.5"):
        return number / Decimal(100)
    return number


def coerce_date(value: object, *, dayfirst: bool = True) -> date | None:
    """Parse business dates: date/datetime objects, Excel serials, ISO, d/m/y.

    ``dayfirst`` resolves slashed-date ambiguity and is a per-institution
    mapping option; when the day field exceeds 12 the unambiguous reading wins
    regardless of preference.
    """
    if is_null_like(value):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, int | float) and not isinstance(value, bool):
        if _SERIAL_MIN <= value <= _SERIAL_MAX:
            return _EXCEL_EPOCH + timedelta(days=int(value))
        raise CoercionError("date", value, "number outside the plausible Excel serial range")
    if isinstance(value, str):
        text = value.strip()
        iso = _ISO_DATE.match(text)
        if iso:
            try:
                return date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
            except ValueError as exc:
                raise CoercionError("date", value, "invalid calendar date") from exc
        slashed = _SLASHED_DATE.match(text)
        if slashed:
            first, second, year = (int(group) for group in slashed.groups())
            day, month = (first, second) if dayfirst else (second, first)
            if month > 12 and day <= 12:
                day, month = month, day
            try:
                return date(year, month, day)
            except ValueError as exc:
                raise CoercionError("date", value, "invalid calendar date") from exc
        raise CoercionError("date", value, "unrecognized format")
    raise CoercionError("date", value, f"unsupported type {type(value).__name__}")


def coerce_int(value: object) -> int | None:
    if is_null_like(value):
        return None
    if isinstance(value, bool):
        raise CoercionError("integer", value, "booleans are not integers")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError as exc:
            raise CoercionError("integer", value, "not an integer") from exc
    raise CoercionError("integer", value, f"unsupported type {type(value).__name__}")


def excel_serial_for(target: date) -> int:
    """The Excel serial number for a date (test and fixture helper)."""
    return (target - _EXCEL_EPOCH).days


def utc_now_stamp() -> datetime:
    return datetime.now(UTC)
