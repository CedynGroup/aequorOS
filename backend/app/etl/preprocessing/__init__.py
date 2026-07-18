"""Deterministic, sanctioned preprocessing stages for the ML-ETL layer.

Three families of :class:`~app.etl.contracts.Preprocessor`, all rule-based (no ML — these
are audit-defensible by construction) and all routed through the guard-alias resolver so no
stage can silently modify a regulatory-critical value:

* ``normalizers`` — ISO 4217 / 3166 / 8601 + text (case/whitespace/unicode NFC);
* ``type_coercion`` — percent, thousands separators, null sentinels, Excel serial dates;
* ``reference_resolution`` — institution product code -> canonical regulatory category.
"""

from __future__ import annotations

from app.etl.preprocessing.normalizers import (
    CountryNormalizer,
    CurrencyNormalizer,
    DateNormalizer,
    TextNormalizer,
)
from app.etl.preprocessing.reference_resolution import ReferenceResolver, build_reference_resolver
from app.etl.preprocessing.type_coercion import TypeCoercer

__all__ = [
    "CountryNormalizer",
    "CurrencyNormalizer",
    "DateNormalizer",
    "ReferenceResolver",
    "TextNormalizer",
    "TypeCoercer",
    "build_reference_resolver",
]
