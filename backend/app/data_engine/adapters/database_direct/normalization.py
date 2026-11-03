"""Normalize source-native cell values into canonical-friendly, JSON-safe forms.

Core banking databases across African and emerging markets carry three
recurring hazards the file adapters never see, because a DBAPI hands back the
raw storage form:

1. **Non-standard charsets.** Legacy cores store names in Windows-1252, ISO-
   8859-x, or a national code page; a driver returns them as ``bytes`` (or a
   mojibake ``str``). We decode with the bank's configured source encoding.
2. **Non-UTC, timezone-naive timestamps.** A core stamps local wall-clock time
   with no offset. We attach the bank's configured source timezone and convert
   to UTC so ``ingested_at``/business-time reasoning is unambiguous.
3. **Locale number formats.** ``"1.234.567,89"`` (decimal comma, dot grouping)
   must not be read as ``1.234``. We normalize grouping/decimal separators per
   the bank's configured locale before the translator coerces to ``Decimal``.

Everything here is a pure function of value + a small, config-driven
:class:`NormalizationPolicy`, so a staged bundle is fully reproducible and the
normalization travels with the recorded config version. The output is JSON-
serializable (so it can be staged and re-read offline): ``datetime``/``date`` as
ISO strings, ``Decimal`` preserved as a numeric string, ``bytes`` decoded, other
scalars passed through.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict


class NormalizationPolicy(BaseModel):
    """Per-bank normalization rules, resolved from the extraction config.

    ``source_encoding`` decodes ``bytes`` columns. ``source_utc_offset_minutes``
    localizes timezone-naive timestamps (e.g. ``0`` for UTC, ``60`` for UTC+1,
    ``-300`` for UTC-5); ``None`` leaves naive timestamps naive (ISO without an
    offset). ``decimal_separator``/``grouping_separator`` normalize locale
    number text before decimal coercion.
    """

    model_config = ConfigDict(frozen=True)

    source_encoding: str = "utf-8"
    source_utc_offset_minutes: int | None = None
    decimal_separator: str = "."
    grouping_separator: str = ","
    # Columns whose values are known locale-formatted numbers held as text; only
    # these are separator-normalized, so a genuine string id like "1,234-A" is
    # never mangled. Empty = apply number normalization to no columns (safe
    # default; onboarding opts specific numeric-text columns in).
    locale_number_columns: tuple[str, ...] = ()

    @property
    def source_timezone(self) -> timezone | None:
        if self.source_utc_offset_minutes is None:
            return None
        return timezone(timedelta(minutes=self.source_utc_offset_minutes))


def normalize_row(row: dict[str, Any], policy: NormalizationPolicy) -> dict[str, Any]:
    """Normalize every cell in a row into a JSON-safe, canonical-friendly form."""
    number_columns = set(policy.locale_number_columns)
    return {
        column: normalize_value(value, policy, is_number_text=column in number_columns)
        for column, value in row.items()
    }


def normalize_value(  # noqa: PLR0911 - one branch per source-native value form
    value: Any, policy: NormalizationPolicy, *, is_number_text: bool = False
) -> Any:
    """Normalize a single cell value. Pure; JSON-serializable output."""
    if value is None:
        return None
    if isinstance(value, bytes):
        value = _decode_bytes(value, policy.source_encoding)
    if isinstance(value, datetime):
        return _to_utc_iso(value, policy)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        # Preserve exact precision as text; the translator re-parses to Decimal.
        return format(value, "f")
    if isinstance(value, str):
        if is_number_text:
            return _normalize_number_text(value, policy)
        return value
    # int / float / bool pass through as JSON-native scalars.
    return value


def _decode_bytes(raw: bytes, encoding: str) -> str:
    """Decode a non-UTF charset column, replacing undecodable bytes rather than
    failing the whole pull on one bad string."""
    try:
        return raw.decode(encoding)
    except (LookupError, UnicodeDecodeError):
        return (
            raw.decode(encoding, errors="replace")
            if _is_known(encoding)
            else raw.decode("utf-8", errors="replace")
        )


def _is_known(encoding: str) -> bool:
    try:
        "".encode(encoding)
    except LookupError:
        return False
    return True


def _to_utc_iso(value: datetime, policy: NormalizationPolicy) -> str:
    """Attach the source timezone to a naive timestamp and convert to UTC.

    A timezone-aware value is converted to UTC directly; a naive value with no
    configured source offset is returned as a naive ISO string (offset unknown,
    and inventing one would be worse than declaring none).
    """
    if value.tzinfo is not None:
        return value.astimezone(UTC).isoformat()
    tz = policy.source_timezone
    if tz is None:
        return value.isoformat()
    return value.replace(tzinfo=tz).astimezone(UTC).isoformat()


def _normalize_number_text(text: str, policy: NormalizationPolicy) -> str:
    """Rewrite locale-formatted numeric text to a ``.``-decimal, ungrouped form.

    ``"1.234.567,89"`` with (decimal ``,`` grouping ``.``) -> ``"1234567.89"``.
    Non-numeric-looking text is returned unchanged so ids are never mangled.
    """
    stripped = text.strip()
    if not stripped:
        return text
    body = stripped[1:] if stripped[0] in "+-" else stripped
    allowed = set("0123456789") | {policy.decimal_separator, policy.grouping_separator}
    if not body or any(ch not in allowed for ch in body):
        return text
    sign = stripped[0] if stripped[0] in "+-" else ""
    without_grouping = body.replace(policy.grouping_separator, "")
    canonical = without_grouping.replace(policy.decimal_separator, ".")
    return f"{sign}{canonical}"
