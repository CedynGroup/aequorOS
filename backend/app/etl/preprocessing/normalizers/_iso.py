"""Deterministic ISO reference tables for the sanctioned normalizers.

Bundled rather than pulled from a runtime dependency so normalization is reproducible and
audit-defensible with no network or optional-package surface (``data_engine.md`` §12.5:
every transform must be explainable to an examiner). The tables cover the ISO 4217 active
currency codes and an ISO 3166-1 name/alpha-3 -> alpha-2 index weighted to the service's
operating region (all of Africa plus the major global economies), which is sufficient for
the Sample Bank reference dataset and every jurisdiction in the target segment.
"""

from __future__ import annotations

import re
import unicodedata

# ISO 4217 active alphabetic currency codes (subset covering all realistic bank exposures;
# extend as vendor coverage grows — an unknown code is FLAGGED, never coerced).
ISO_4217_CURRENCIES: frozenset[str] = frozenset(
    {
        "AED", "AOA", "ARS", "AUD", "BHD", "BRL", "BWP", "CAD", "CHF", "CNY",
        "CVE", "CZK", "DKK", "DZD", "EGP", "ETB", "EUR", "GBP", "GHS", "GMD",
        "GNF", "HKD", "HUF", "IDR", "ILS", "INR", "JPY", "KES", "KRW", "KWD",
        "LRD", "LSL", "LYD", "MAD", "MGA", "MRU", "MUR", "MWK", "MXN", "MYR",
        "MZN", "NAD", "NGN", "NOK", "NZD", "OMR", "PHP", "PLN", "QAR", "RUB",
        "RWF", "SAR", "SCR", "SDG", "SEK", "SGD", "SLE", "SSP", "SZL", "THB",
        "TND", "TRY", "TZS", "UGX", "USD", "XAF", "XOF", "ZAR", "ZMW", "ZWG",
    }
)

# A few common non-ISO spellings a spreadsheet might carry, mapped to their ISO code. This
# is an *exact, curated* table — not fuzzy inference — so the currency rewrite stays a
# value-preserving format normalization (the same currency, canonically spelled).
_CURRENCY_SYNONYMS: dict[str, str] = {
    "GH¢": "GHS",
    "GHC": "GHS",
    "CEDI": "GHS",
    "CEDIS": "GHS",
    "GHANA CEDI": "GHS",
    "US$": "USD",
    "USD$": "USD",
    "DOLLAR": "USD",
    "DOLLARS": "USD",
    "£": "GBP",
    "€": "EUR",
    "¥": "JPY",
    "NAIRA": "NGN",
    "RAND": "ZAR",
}

# ISO 3166-1 alpha-2 codes (the normalization target set).
_ISO_3166_ALPHA2: frozenset[str] = frozenset(
    {
        "AE", "AO", "AR", "AU", "BF", "BH", "BJ", "BR", "BW", "CA", "CD", "CF", "CG",
        "CH", "CI", "CM", "CN", "CV", "CZ", "DE", "DK", "DZ", "EG", "ER", "ES",
        "ET", "FR", "GA", "GB", "GH", "GM", "GN", "GQ", "GW", "HK", "HU", "ID",
        "IL", "IN", "IT", "JP", "KE", "KM", "KR", "KW", "LR", "LS", "LY", "MA",
        "MG", "ML", "MR", "MU", "MW", "MX", "MY", "MZ", "NA", "NE", "NG", "NL",
        "NO", "NZ", "OM", "PH", "PL", "QA", "RU", "RW", "SA", "SC", "SD", "SE",
        "SG", "SL", "SN", "SO", "SS", "SZ", "TD", "TG", "TH", "TN", "TR", "TZ",
        "UG", "US", "ZA", "ZM", "ZW",
    }
)

# ISO 3166-1 country name and alpha-3 -> alpha-2 index. Names are stored upper-cased and
# accent-folded for tolerant lookup (see :func:`normalize_country_key`).
_ISO_3166_INDEX: dict[str, str] = {
    # alpha-3 -> alpha-2
    "GHA": "GH", "NGA": "NG", "KEN": "KE", "ZAF": "ZA", "USA": "US", "GBR": "GB",
    "DEU": "DE", "FRA": "FR", "CHN": "CN", "IND": "IN", "JPN": "JP", "CIV": "CI",
    "TGO": "TG", "BEN": "BJ", "BFA": "BF", "MLI": "ML",
    "SEN": "SN", "CMR": "CM", "EGY": "EG", "MAR": "MA", "TZA": "TZ", "UGA": "UG",
    "RWA": "RW", "ETH": "ET", "AGO": "AO", "MOZ": "MZ", "ZMB": "ZM", "ZWE": "ZW",
    "NER": "NE", "TCD": "TD", "COD": "CD", "COG": "CG", "GAB": "GA", "GNB": "GW",
    "GIN": "GN", "LBR": "LR", "SLE": "SL", "GMB": "GM", "MRT": "MR", "AUS": "AU",
    "CAN": "CA", "BRA": "BR", "ARE": "AE", "SAU": "SA", "QAT": "QA", "KWT": "KW",
    "SGP": "SG", "HKG": "HK", "MYS": "MY", "IDN": "ID", "PHL": "PH", "KOR": "KR",
    "TUR": "TR", "RUS": "RU", "NLD": "NL", "ITA": "IT", "ESP": "ES", "CHE": "CH",
    "SWE": "SE", "NOR": "NO", "DNK": "DK", "POL": "PL", "CZE": "CZ", "HUN": "HU",
    # common English names -> alpha-2
    "GHANA": "GH", "NIGERIA": "NG", "KENYA": "KE", "SOUTH AFRICA": "ZA",
    "UNITED STATES": "US", "UNITED STATES OF AMERICA": "US",
    "UNITED KINGDOM": "GB", "GREAT BRITAIN": "GB", "ENGLAND": "GB",
    "GERMANY": "DE", "FRANCE": "FR", "CHINA": "CN", "INDIA": "IN", "JAPAN": "JP",
    "IVORY COAST": "CI", "COTE DIVOIRE": "CI", "TOGO": "TG", "BENIN": "BJ",
    "BURKINA FASO": "BF", "MALI": "ML", "SENEGAL": "SN", "CAMEROON": "CM",
    "EGYPT": "EG", "MOROCCO": "MA", "TANZANIA": "TZ", "UGANDA": "UG",
    "RWANDA": "RW", "ETHIOPIA": "ET", "ANGOLA": "AO", "MOZAMBIQUE": "MZ",
    "ZAMBIA": "ZM", "ZIMBABWE": "ZW", "NIGER": "NE", "CHAD": "TD",
    "LIBERIA": "LR", "SIERRA LEONE": "SL", "GAMBIA": "GM", "THE GAMBIA": "GM",
    "MAURITANIA": "MR", "GUINEA": "GN", "AUSTRALIA": "AU", "CANADA": "CA",
    "BRAZIL": "BR", "UNITED ARAB EMIRATES": "AE", "SAUDI ARABIA": "SA",
    "QATAR": "QA", "KUWAIT": "KW", "SINGAPORE": "SG", "HONG KONG": "HK",
    "MALAYSIA": "MY", "INDONESIA": "ID", "PHILIPPINES": "PH", "SOUTH KOREA": "KR",
    "KOREA": "KR", "TURKEY": "TR", "RUSSIA": "RU", "NETHERLANDS": "NL",
    "ITALY": "IT", "SPAIN": "ES", "SWITZERLAND": "CH", "SWEDEN": "SE",
    "NORWAY": "NO", "DENMARK": "DK", "POLAND": "PL",
}


def _fold(text: str) -> str:
    """Upper-case, accent-fold, strip non-alphanumerics-to-space, collapse whitespace."""
    folded = unicodedata.normalize("NFKD", text)
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    folded = re.sub(r"[^A-Za-z0-9]+", " ", folded).strip().upper()
    return re.sub(r"\s+", " ", folded)


def normalize_currency(raw: str) -> str | None:
    """Return the canonical ISO 4217 code for ``raw``, or ``None`` if unrecognised."""
    candidate = raw.strip().upper()
    if candidate in ISO_4217_CURRENCIES:
        return candidate
    folded = _fold(raw)
    if folded in ISO_4217_CURRENCIES:
        return folded
    return _CURRENCY_SYNONYMS.get(candidate) or _CURRENCY_SYNONYMS.get(folded)


def normalize_country(raw: str) -> str | None:
    """Return the ISO 3166-1 alpha-2 code for ``raw`` (code or name), or ``None``."""
    candidate = raw.strip().upper()
    if candidate in _ISO_3166_ALPHA2:
        return candidate
    folded = _fold(raw)
    if folded in _ISO_3166_ALPHA2:
        return folded
    return _ISO_3166_INDEX.get(folded)
