"""Position / entity ``attributes`` captured from source columns (pure).

The canonical record contracts carry a free-form ``attributes`` payload for
what the typed schema has no home for — the BoG prudential-return conventions
(``sector``, ``bog_classification``, ``interest_in_suspense_ghs``, …) among
them (``docs/API_INTEGRATION.md`` §3.4). Two conventions put a source column
into that payload, and both adapters (Excel/CSV upload and API push) apply
them through :func:`attributes_from_columns` so a loans sheet and a loans
push land the SAME attribute keys:

1. ``EntityMapping.attribute_columns`` — an onboarding mapping lists source
   columns to copy verbatim under their own header (``"ECL_PROV"`` →
   ``attributes["ECL_PROV"]``).
2. **``attributes.<key>`` column headers** — a source column named with the
   ``attributes.`` prefix is captured under ``<key>`` with NO mapping-config
   change (``"attributes.sector"`` → ``attributes["sector"]``). This is the
   convention of the loans template (``onboarding/sample_bank/loans_template.csv``)
   and of ``scripts/ingest_push.py``; a bank exporting a sheet with these
   headers is zero-config on either ingestion path.

Values are returned raw; the caller stringifies/coerces per its own rules
(the Excel adapter stringifies like it does for ``attribute_columns``).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any

#: Column-header prefix that routes a source column into ``attributes``.
ATTRIBUTE_COLUMN_PREFIX = "attributes."


def _never_null(value: Any) -> bool:
    return value is None


def attribute_key(column: str) -> str | None:
    """``"attributes.sector"`` → ``"sector"``; ``None`` for any other header
    (including a bare ``"attributes."`` with no key)."""
    if not isinstance(column, str):
        return None
    if not column.startswith(ATTRIBUTE_COLUMN_PREFIX):
        return None
    key = column[len(ATTRIBUTE_COLUMN_PREFIX) :].strip()
    return key or None


def attributes_from_columns(
    row: Mapping[str, Any],
    attribute_columns: Iterable[str] = (),
    *,
    is_null: Callable[[Any], bool] = _never_null,
) -> dict[str, Any]:
    """The ``attributes`` payload one source row contributes.

    ``attribute_columns`` (mapping-config list) are copied under their own
    header; every ``attributes.<key>`` column is copied under ``<key>``.
    Null-like cells (per ``is_null``) are omitted — an absent attribute is
    "not stated", never an empty string. When both conventions name the same
    key the prefixed column wins (it is the more explicit statement).
    """
    captured: dict[str, Any] = {}
    for column in attribute_columns:
        if column in row and not is_null(row[column]):
            captured[column] = row[column]
    for column, value in row.items():
        key = attribute_key(column)
        if key is not None and not is_null(value):
            captured[key] = value
    return captured
