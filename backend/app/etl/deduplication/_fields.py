"""Source-agnostic field access for deduplication over :class:`RawRecord`.

Deduplicators operate on the adapter's post-``extract`` ``RawRecord`` shape, whose
``data`` dict carries *raw source column names*. Different sources spell the same
concept differently (``counterparty_name`` vs ``name`` vs ``customer_name``), so this
module resolves a small set of canonical concepts through alias lists — case- and
separator-insensitively — without ever reaching into source-system semantics
(``data_engine.md`` §2.1). Missing concepts return ``None``; nothing is invented.
"""

from __future__ import annotations

import re
import unicodedata
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from app.domain.ingestion.contracts import RawRecord

# Canonical concept -> ordered candidate source column names (first present wins).
_ALIASES: dict[str, tuple[str, ...]] = {
    "id": ("counterparty_id", "position_id", "customer_id", "party_id", "id"),
    "name": ("counterparty_name", "name", "customer_name", "legal_name", "party_name"),
    "national_id": (
        "national_id",
        "ghana_card",
        "ghana_card_number",
        "tin",
        "tax_id",
        "tax_identification_number",
        "passport_number",
        "id_number",
    ),
    "address": ("address", "residential_address", "postal_address", "street_address", "location"),
    "account_number": ("account_number", "account_no", "iban", "wallet_id", "msisdn"),
    "country": ("country", "country_code", "domicile", "nationality"),
    "type": ("counterparty_type", "type", "party_type", "customer_type"),
    "source_reference": ("source_reference", "external_reference", "arrangement_id"),
    "as_of_date": ("as_of_date", "as_of", "snapshot_date", "reporting_date", "position_date"),
    "source_system": ("source_system", "system", "origin_system"),
    "balance": ("balance_ghs", "balance", "balance_ccy", "outstanding_amount", "gl_balance"),
    "notional": ("notional_ccy", "notional", "notional_amount"),
    "currency": ("currency", "ccy"),
}


def _normalize_key(key: str) -> str:
    """Lowercase and strip non-alphanumerics for tolerant header matching."""
    return re.sub(r"[^a-z0-9]", "", key.lower())


def get_field(record: RawRecord, concept: str) -> str | None:
    """Return the first populated source value for ``concept`` as a string, else None."""
    aliases = _ALIASES.get(concept, (concept,))
    data = record.data
    # Fast path: exact source column present.
    for alias in aliases:
        if alias in data and _is_populated(data[alias]):
            return str(data[alias]).strip()
    # Tolerant path: normalized header equality.
    normalized = {_normalize_key(k): v for k, v in data.items()}
    for alias in aliases:
        value = normalized.get(_normalize_key(alias))
        if _is_populated(value):
            return str(value).strip()
    return None


def _is_populated(value: object) -> bool:
    if value is None:
        return False
    text = str(value).strip()
    return text != "" and text.lower() not in {"nan", "none", "null", "n/a"}


def record_id(record: RawRecord) -> str:
    """A stable id for linkage records: source id if present, else the locator."""
    return get_field(record, "id") or record.source_locator


def normalize_name(value: str | None) -> str:
    """Fold a party name for comparison: unicode-normalize, lowercase, drop legal
    suffixes and punctuation, collapse whitespace. Never mutates the source value —
    this is a *comparison key*, not a sanctioned rewrite."""
    if not value:
        return ""
    text = unicodedata.normalize("NFKD", value)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    tokens = [t for t in text.split() if t and t not in _LEGAL_SUFFIXES]
    return " ".join(tokens)


# Common corporate-form tokens folded out before name comparison (they carry no
# entity-identity signal: "ACME TRADING LTD" and "Acme Trading Limited" are one entity).
_LEGAL_SUFFIXES: frozenset[str] = frozenset(
    {
        "ltd",
        "limited",
        "plc",
        "llc",
        "inc",
        "incorporated",
        "co",
        "company",
        "corp",
        "corporation",
        "gh",
        "ghana",
        "grp",
        "group",
        "holdings",
        "enterprise",
        "enterprises",
        "ventures",
        "and",
        "the",
    }
)


def digits_only(value: str | None) -> str:
    """Strip everything but digits — for national-id/account numeric comparison."""
    if not value:
        return ""
    return re.sub(r"\D", "", value)


def token_bag(value: str | None) -> frozenset[str]:
    """Set of significant tokens from a free-text field (e.g. an address)."""
    if not value:
        return frozenset()
    text = unicodedata.normalize("NFKD", value).lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return frozenset(t for t in text.split() if len(t) > 1)


def jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    """Jaccard similarity of two token iterables; 0.0 when either is empty."""
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)
