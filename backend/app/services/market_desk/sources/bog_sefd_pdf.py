"""BoG "Summary of Economic and Financial Data" bimonthly PDF (README §3).

The publication is a fixed ~14-section statistical annex. This parser
extracts the two interest-rate sections for real:

- **3a. Interest Rates (Percent Per Annum)** — MPR, interbank weighted
  average, 91/182/364-day bills (interest equivalent), deposit rates, time
  deposits, average lending rate, Ghana Reference Rate;
- **3b. Interest Rates — Secondary Market** — post-DDEP 4..15-year bond
  yields (source: GFIM).

Every other section (prices, real sector, exchange rates, commodities,
external, fiscal, monetary, banking, capital markets, payments) is listed in
``UNSUPPORTED_SECTIONS`` and reported as an explicit warning — honesty over
coverage; those tables need their own templated parsers before their numbers
can be trusted.

Captured-fixture quirks: month columns are ``YYYY:MM``; the vertical
"PUBLIC" watermark drops stray letters INTO numeric cells (``15L.56``,
``B16.87``) — cleaned via ``pdf_text.clean_numeric_token`` and flagged.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

from app.services.market_desk.sources.core import (
    QF_WATERMARK_CLEANED,
    ObservationDraft,
    ParseContext,
    ParseResult,
    slugify,
)
from app.services.market_desk.sources.pdf_text import clean_numeric_token, page_lines

_MONTH_COL_RE = re.compile(r"^(\d{4}):(\d{2})$")
_SECTION_3A_RE = re.compile(r"3a\.\s*Interest\s+Rates", re.IGNORECASE)
_SECTION_3B_RE = re.compile(r"3b\.\s*Interest\s+Rates.*Secondary", re.IGNORECASE)

# Row labels worth publishing from 3a, with stable slugs. Anything else on
# the page (subsection headings like "Interbank" / "Deposit Rates") is
# structure, not data.
_SECTION_3A_ROWS: tuple[tuple[str, str], ...] = (
    ("Monetary Policy Rate", "MPR"),
    ("Interbank Weighted Average", "INTERBANK_WAVG"),
    ("91-Day Bill", "TBILL_91"),
    ("182-Day Bill", "TBILL_182"),
    ("364-Day Bill", "TBILL_364"),
    ("Demand Deposits", "DEMAND_DEPOSITS"),
    ("Savings Deposits", "SAVINGS_DEPOSITS"),
    ("3-months", "TIME_DEPOSIT_3M"),
    ("6-months", "TIME_DEPOSIT_6M"),
    ("Average Lending Rate", "LENDING_AVG"),
    ("Ghana Reference Rate", "GRR"),
)

_BOND_ROW_RE = re.compile(r"^(\d{1,2})-Year\s+Bond\b", re.IGNORECASE)

UNSUPPORTED_SECTIONS: tuple[str, ...] = (
    "1. Price Developments",
    "2. Real Sector Indicators",
    "4. Exchange Rates",
    "5. Commodity Prices",
    "6. External Sector Developments",
    "7. Government Fiscal Operations",
    "8a. Monetary Indicators (Levels)",
    "8b. Monetary Indicators (growth rates)",
    "9. Banking Sector Indicators",
    "10. Capital Market Performance",
    "11. Payment Systems Data",
    "12. Payment Systems Data (continued)",
)


# Stray single watermark letters land as their own tokens on some lines
# ("Interbank ... I"); they carry no information and are dropped everywhere.
_WATERMARK_TOKEN_RE = re.compile(r"^[PUBLIC]$")
# A parenthetical qualifier directly after a row label ("(interest
# equivalent)") is part of the label, not a value.
_LABEL_QUALIFIER_RE = re.compile(r"^\s*\([^)]*\)")


def _tokens(line: str) -> list[str]:
    return [token for token in line.split() if not _WATERMARK_TOKEN_RE.match(token)]


def _month_columns(lines: list[str]) -> list[date]:
    """The single header line of ``YYYY:MM`` tokens, as first-of-month dates."""
    for line in lines:
        tokens = _tokens(line)
        months = [_MONTH_COL_RE.match(token) for token in tokens]
        if len(tokens) >= 6 and all(months):
            return [date(int(m.group(1)), int(m.group(2)), 1) for m in months if m]
    return []


def _row_values(
    tail: str, months: list[date], result: ParseResult, *, label: str
) -> list[tuple[date, str, bool]]:
    """Zip a row's numeric tail against the month columns; refuse (with a
    warning, never a guess) when the counts disagree."""
    values: list[tuple[str, bool]] = []
    for token in _tokens(_LABEL_QUALIFIER_RE.sub("", tail)):
        value, cleaned = clean_numeric_token(token)
        if value is None:
            return []  # a non-numeric token means this is not a data row
        values.append((str(value), cleaned))
    if not values:
        return []
    if len(values) != len(months):
        result.warnings.append(
            f"{label}: {len(values)} values against {len(months)} month columns — row skipped"
        )
        return []
    return [(month, value, cleaned) for month, (value, cleaned) in zip(months, values, strict=True)]


def parse_sefd_pdf(  # noqa: PLR0912 - two sections x row dispatch, kept in one pass
    raw: bytes, *, context: ParseContext
) -> ParseResult:
    """Extract sections 3a/3b -> GHS.SEFD.{SLUG} / GHS.SEFD.BOND.{n}Y."""
    del context
    result = ParseResult()
    pages = page_lines(raw)

    for lines in pages:
        is_3a = any(_SECTION_3A_RE.search(line) for line in lines[:6])
        is_3b = any(_SECTION_3B_RE.search(line) for line in lines[:6])
        if not is_3a and not is_3b:
            continue
        # 3b's heading also matches the 3a regex; disambiguate.
        if is_3b:
            is_3a = False
        months = _month_columns(lines)
        if not months:
            result.errors.append("interest-rate section found but no YYYY:MM header row")
            continue

        if is_3a:
            for label, slug in _SECTION_3A_ROWS:
                for line in lines:
                    if not line.startswith(label):
                        continue
                    cells = _row_values(
                        line[len(label):], months, result, label=f"3a {label}"
                    )
                    if not cells:
                        continue
                    _emit(result, f"GHS.SEFD.{slug}", label, cells, section="3a")
                    break
        else:
            for line in lines:
                bond = _BOND_ROW_RE.match(line)
                if not bond:
                    continue
                years = int(bond.group(1))
                label = f"{years}-Year Bond"
                cells = _row_values(line[bond.end():], months, result, label=f"3b {label}")
                if not cells:
                    continue
                _emit(result, f"GHS.SEFD.BOND.{years}Y", label, cells, section="3b")

    if not result.observations:
        result.errors.append("no interest-rate observations parsed from SEFD PDF")
    result.warnings.extend(
        f"section not extracted (unsupported): {section}" for section in UNSUPPORTED_SECTIONS
    )
    return result


def _emit(
    result: ParseResult,
    series_code: str,
    label: str,
    cells: list[tuple[date, str, bool]],
    *,
    section: str,
) -> None:
    for month, value, cleaned in cells:
        draft = ObservationDraft(
            series_code=series_code,
            as_of_date=month,
            value=Decimal(value),
            unit="pct",
            attributes={"row_label": label, "section": section, "slug": slugify(label)},
        )
        if cleaned:
            draft.flag(QF_WATERMARK_CLEANED)
        result.observations.append(draft)
