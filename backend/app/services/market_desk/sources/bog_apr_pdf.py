"""BoG "Annual Percentage Rates (APR) of Banks" monthly PDF (README §3).

The competitor-lending-rate source (spec §3 item 9, §12): nine templated
tables — Households / SMEs / Corporates × 1 / 3 / 5-year tenor — each listing
every bank's GRR, spread, average lending rate, fee columns, and the Average
Facility APR. Emits ``GHS.APR.{BANK_SLUG}.{SEGMENT_TENOR}`` observations
whose value is the Average Facility APR (the number the notice exists to
publish); the rate build-up rides in attributes.

Captured-fixture quirks this parser encodes:
- three of the nine table pages are rendered with per-glyph positioning
  (pypdf and even pdfplumber's layout mode shatter them) — lines are
  reconstructed from character geometry (``pdf_text.page_lines``);
- long bank names wrap: the rank+numbers line is preceded/followed by
  name-fragment lines that carry no numbers, so fragments are stitched;
- ``NL`` means "no loan of this tenor granted this month" — no observation,
  counted per table in warnings (never invented as zero);
- ``N/A`` fee cells are simply absent fees;
- the report month comes from each table's NL footnote
  ("... in the month of May, 2026").
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal

from app.services.market_desk.sources.core import (
    ObservationDraft,
    ParseContext,
    ParseResult,
    slugify,
)
from app.services.market_desk.sources.pdf_text import page_lines

_TABLE_HEADER_RE = re.compile(r"^Table\s+(\d+)$", re.IGNORECASE)
_SEGMENT_RE = re.compile(r"Loans?\s+to\s+(Households|SMEs|Corporates)", re.IGNORECASE)
_TENOR_RE = re.compile(r"Tenor\s+of\s+Facility:\s*(\d+)\s*year", re.IGNORECASE)
_MONTH_RE = re.compile(r"month\s+of\s+([A-Za-z]+),?\s+(\d{4})")
# Rank + everything + at least the GRR/spread/APR numeric tail.
_DATA_ROW_RE = re.compile(r"^(\d{1,2})\s+(.*)$")
_CELL_RE = re.compile(r"(-?\d+\.\d+|N/?A|NL)")

_SEGMENT_SLUGS = {"HOUSEHOLDS": "HOUSEHOLD", "SMES": "SME", "CORPORATES": "CORPORATE"}

# The notice renders the SAME bank's name differently across pages — inline
# rows drop the corporate suffix ("Standard Chartered Bank (Ghana)") while
# wrapped rows keep it ("... (Ghana) Limited"). The series identity must not
# fork on that, so slugs drop trailing corporate suffixes; the name as
# published on that page stays in attributes.
_CORPORATE_SUFFIXES = {"LIMITED", "LTD", "PLC"}


def bank_slug(name: str) -> str:
    tokens = slugify(name).split("_")
    while tokens and tokens[-1] in _CORPORATE_SUFFIXES:
        tokens.pop()
    return "_".join(tokens) if tokens else slugify(name)


def _month_first_day(month_name: str, year: str) -> date:
    return datetime.strptime(f"01 {month_name.title()} {year}", "%d %B %Y").date()


def parse_apr_pdf(raw: bytes, *, context: ParseContext) -> ParseResult:  # noqa: PLR0912, PLR0915
    """Parse all nine APR tables into per-bank, per-category observations."""
    del context
    result = ParseResult()
    report_month: date | None = None

    for lines in page_lines(raw):
        segment: str | None = None
        tenor_years: int | None = None
        for line in lines:
            seg_match = _SEGMENT_RE.search(line)
            if seg_match:
                segment = _SEGMENT_SLUGS.get(seg_match.group(1).upper())
            tenor_match = _TENOR_RE.search(line)
            if tenor_match:
                tenor_years = int(tenor_match.group(1))
            month_match = _MONTH_RE.search(line)
            if month_match and report_month is None:
                report_month = _month_first_day(month_match.group(1), month_match.group(2))
        if segment is None or tenor_years is None:
            continue  # summary/preamble page, not a bank table

        category = f"{segment}_{tenor_years}Y"
        no_loan_banks = 0
        parsed_rows = 0
        for index, line in enumerate(lines):
            row_match = _DATA_ROW_RE.match(line)
            if not row_match:
                continue
            body = row_match.group(2)
            cells = _CELL_RE.findall(body)
            if len(cells) < 4:
                continue  # header/footnote noise, not a data row
            name = _CELL_RE.split(body)[0].strip()
            if not name:
                # Wrapped name: fragments live on the surrounding lines that
                # carry no numeric cells at all.
                fragments: list[str] = []
                for neighbor in (index - 1, index + 1):
                    if 0 <= neighbor < len(lines):
                        candidate = lines[neighbor].strip()
                        if candidate and not _CELL_RE.search(candidate):
                            fragments.append(candidate)
                name = " ".join(fragments)
            if not name:
                result.warnings.append(f"{category}: could not resolve bank name in {line!r}")
                continue

            values = [c if c not in {"N/A", "NA"} else None for c in cells]
            apr_cell = values[-1]
            if apr_cell is None or apr_cell == "NL":
                no_loan_banks += 1
                continue
            grr = values[0] if values[0] not in {None, "NL"} else None
            spread = values[1] if len(values) > 1 and values[1] not in {None, "NL"} else None
            lending = values[2] if len(values) > 2 and values[2] not in {None, "NL"} else None
            attributes: dict[str, object] = {
                "bank": name,
                "segment": segment,
                "tenor_years": tenor_years,
            }
            if grr is not None:
                attributes["grr"] = grr
            if spread is not None:
                attributes["spread"] = spread
            if lending is not None:
                attributes["avg_lending_rate"] = lending
            result.observations.append(
                ObservationDraft(
                    series_code=f"GHS.APR.{bank_slug(name)}.{category}",
                    as_of_date=date(1900, 1, 1),  # patched below once month known
                    value=Decimal(apr_cell),
                    unit="pct",
                    attributes=attributes,
                )
            )
            parsed_rows += 1
        if no_loan_banks:
            result.warnings.append(
                f"{category}: {no_loan_banks} bank(s) reported NL (no loan granted)"
            )
        if parsed_rows == 0:
            result.warnings.append(f"{category}: no bank rows parsed")

    if report_month is None:
        result.errors.append("report month not found (no 'month of <Month>, <Year>' footnote)")
        result.observations = []
        return result
    for draft in result.observations:
        draft.as_of_date = report_month
        draft.attributes["report_month"] = report_month.strftime("%Y-%m")
    if not result.observations:
        result.errors.append("no APR observations parsed")
    return result
