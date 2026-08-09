"""BoG auction-result parsers (fixtures README §2).

Tender pages carry NO inline tables — the security breakdown lives only in a
linked PDF under ``wp-content/uploads/``, so the HTML side of this module is
discovery metadata for the fetch layer (tender number, held date, PDF URL)
and the PDF side emits the observations.

PDF quirks encoded from the captured notices:
- amounts are ``GH¢ 3,701.66`` — currency sign + thousands separators;
- rate ranges are inconsistently spaced/hyphenated (``5.4254 – 5.6976``,
  ``5.5000– 5.6795``, ``11.3868-12.0000`` — en-dash or hyphen, spaces
  optional); the tokenizer accepts all of them and yields BOTH bounds;
- GoG notices append a summary of the PRIOR tender and next week's target —
  row scanning stops at the summary heading so last week's totals are never
  mistaken for clearing results;
- a stray ISIN can float outside the table (the captured Auctresult2019 has
  ``GHGGOG059817`` positioned below the grid) — rows are keyed on the
  security name, so orphan ISINs are ignored;
- the same BoG tender number recurs across days in one week (873 ran 03 Aug
  AND 05 Aug 2026) — the held date, not the tender number, is the as-of.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from app.services.market_desk.sources.core import (
    ObservationDraft,
    ParseContext,
    ParseResult,
)
from app.services.market_desk.sources.pdf_text import page_lines

# '5.4254 – 5.6976' | '5.5000– 5.6795' | '11.3868-12.0000' (en-dash or hyphen)
RATE_RANGE_RE = re.compile(r"(\d+\.\d+)\s*[–\-]\s*(\d+\.\d+)")
_SINGLE_RATE_RE = re.compile(r"\b(\d+\.\d{3,4})\b")
_AMOUNT_RE = re.compile(r"GH¢\s*([\d,]+(?:\.\d+)?)")
_HELD_ON_RE = re.compile(
    r"TENDER\s+(\d+)\s+HELD\s+ON\s+(\d{1,2})\s*(?:ST|ND|RD|TH)?\s+([A-Z]+),?\s+(\d{4})",
    re.IGNORECASE,
)
_GOG_ROW_RE = re.compile(r"(\d+)\s*-?\s*Day\s+Bill", re.IGNORECASE)
_BOG_ROW_RE = re.compile(r"(\d+)\s*-?\s*DAY\s+BOG\s+BILL", re.IGNORECASE)
_ISIN_RE = re.compile(r"\b([A-Z]{2}[A-Z0-9]{10})\b")
_TITLE_RE = re.compile(
    r"Results\s+of\s+(GOG\s+)?Tender\s+(\d+)(?:\s+Held\s+On\s+(\d{1,2})\s+([A-Za-z]+)\s+(\d{4}))?",
    re.IGNORECASE,
)
_PDF_LINK_RE = re.compile(r"href=\"([^\"]*wp-content/uploads[^\"]*\.pdf)\"")


@dataclass(frozen=True)
class TenderPage:
    """Discovery metadata scraped from one tender HTML page."""

    tender_number: str
    held_on: date | None
    pdf_url: str | None


def extract_tender_page(html: str) -> TenderPage:
    """Tender number + held date from the page title, plus the result PDF
    link (the only data-bearing artifact on the page)."""
    match = _TITLE_RE.search(html)
    if match is None:
        raise ValueError("page does not look like a BoG tender-results page")
    held_on: date | None = None
    if match.group(3):
        held_on = datetime.strptime(
            f"{match.group(3)} {match.group(4)} {match.group(5)}", "%d %B %Y"
        ).date()
    pdf = _PDF_LINK_RE.search(html)
    return TenderPage(
        tender_number=match.group(2),
        held_on=held_on,
        pdf_url=pdf.group(1) if pdf else None,
    )


def _parse_held_header(lines: list[str]) -> tuple[str, date] | None:
    for line in lines:
        match = _HELD_ON_RE.search(line)
        if match:
            tender = match.group(1)
            held = datetime.strptime(
                f"{match.group(2)} {match.group(3).title()} {match.group(4)}", "%d %B %Y"
            ).date()
            return tender, held
    return None


def _rate_tokens(text: str) -> tuple[list[tuple[Decimal, Decimal]], list[Decimal]]:
    """Split a row's rate area into range tokens and single rates, in order."""
    ranges = [
        (Decimal(m.group(1)), Decimal(m.group(2))) for m in RATE_RANGE_RE.finditer(text)
    ]
    remainder = RATE_RANGE_RE.sub(" ", text)
    singles = [Decimal(m.group(1)) for m in _SINGLE_RATE_RE.finditer(remainder)]
    return ranges, singles


def _security_rows(
    lines: list[str], *, row_re: re.Pattern[str], stop_re: re.Pattern[str] | None
) -> list[tuple[str, int, str]]:
    """(full line, tenor days, line text after the security name)."""
    rows: list[tuple[str, int, str]] = []
    for line in lines:
        if stop_re is not None and stop_re.search(line):
            break
        match = row_re.search(line)
        if not match:
            continue
        rows.append((line, int(match.group(1)), line[match.end():]))
    return rows


_GOG_STOP_RE = re.compile(r"SUMMARY\s+OF\s+TENDER", re.IGNORECASE)


def _emit_security(  # noqa: PLR0913 - one call carries a security's full result
    result: ParseResult,
    *,
    stem: str,
    tenor: int,
    as_of: date,
    attributes: dict[str, object],
    wavg_discount: Decimal,
    wavg_interest: Decimal,
) -> None:
    for leg, value in (("DISCOUNT", wavg_discount), ("INTEREST", wavg_interest)):
        result.observations.append(
            ObservationDraft(
                series_code=f"{stem}.{tenor}.{leg}",
                as_of_date=as_of,
                value=value,
                unit="pct",
                attributes=dict(attributes),
            )
        )


def parse_gog_auction_pdf(raw: bytes, *, context: ParseContext) -> ParseResult:
    """GoG weekly tender notice ("NOTICE ... NO. BG/FMD/...") ->
    GHS.AUCTION.GOG.{tenor}.{DISCOUNT,INTEREST} weighted averages, with
    amounts and all three published rate ranges as attributes."""
    del context
    result = ParseResult()
    lines = [line for page in page_lines(raw) for line in page]
    header = _parse_held_header(lines)
    if header is None:
        result.errors.append("no 'TENDER N HELD ON <date>' header found")
        return result
    tender, held_on = header

    target: str | None = None
    for line in lines:
        if "TARGET FOR" in line.upper():
            amount = _AMOUNT_RE.search(line)
            if amount:
                target = amount.group(1).replace(",", "")
                break

    for line, tenor, _tail in _security_rows(lines, row_re=_GOG_ROW_RE, stop_re=_GOG_STOP_RE):
        isin = _ISIN_RE.search(line)
        amounts = [m.group(1).replace(",", "") for m in _AMOUNT_RE.finditer(line)]
        ranges, singles = _rate_tokens(line)
        if len(ranges) != 3 or len(singles) != 2:
            result.warnings.append(
                f"unexpected rate layout for {tenor}-day row "
                f"({len(ranges)} ranges / {len(singles)} singles): {line!r}"
            )
            continue
        bid_range, allotted_discount, allotted_interest = ranges
        attributes: dict[str, object] = {
            "tender": tender,
            "held_on": held_on.isoformat(),
            "security_type": f"{tenor} DAY BILL",
            "bid_range": [str(bid_range[0]), str(bid_range[1])],
            "allotted_discount_range": [str(allotted_discount[0]), str(allotted_discount[1])],
            "allotted_interest_range": [str(allotted_interest[0]), str(allotted_interest[1])],
        }
        if isin:
            attributes["isin"] = isin.group(1)
        if len(amounts) >= 2:
            attributes["amount_tendered_m_ghs"] = amounts[0]
            attributes["amount_accepted_m_ghs"] = amounts[1]
        if target is not None:
            attributes["weekly_target_m_ghs"] = target
        _emit_security(
            result,
            stem="GHS.AUCTION.GOG",
            tenor=tenor,
            as_of=held_on,
            attributes=attributes,
            wavg_discount=singles[0],
            wavg_interest=singles[1],
        )
    if not result.observations:
        result.errors.append("no per-security clearing rows parsed from GoG auction PDF")
    return result


def parse_bog_auction_pdf(raw: bytes, *, context: ParseContext) -> ParseResult:
    """BoG bill tender notice ("NOTICE ... NO. <N>") ->
    GHS.AUCTION.BOG.{tenor}.{DISCOUNT,INTEREST} weighted averages plus the
    total amount sold."""
    del context
    result = ParseResult()
    lines = [line for page in page_lines(raw) for line in page]
    header = _parse_held_header(lines)
    if header is None:
        result.errors.append("no 'TENDER N HELD ON <date>' header found")
        return result
    tender, held_on = header

    total_sold: str | None = None
    for line in lines:
        if "TOTAL AMOUNT" in line.upper() and "SOLD" in line.upper():
            amount = _AMOUNT_RE.search(line)
            if amount:
                total_sold = amount.group(1).replace(",", "")
                break

    for line, tenor, _tail in _security_rows(lines, row_re=_BOG_ROW_RE, stop_re=None):
        isin = _ISIN_RE.search(line)
        ranges, singles = _rate_tokens(line)
        if len(ranges) != 3 or len(singles) != 2:
            result.warnings.append(
                f"unexpected rate layout for {tenor}-day BoG bill row "
                f"({len(ranges)} ranges / {len(singles)} singles): {line!r}"
            )
            continue
        bid_range, allotted_discount, allotted_interest = ranges
        attributes: dict[str, object] = {
            "tender": tender,
            "held_on": held_on.isoformat(),
            "security_type": f"{tenor} DAY BOG BILL",
            "bid_range": [str(bid_range[0]), str(bid_range[1])],
            "allotted_discount_range": [str(allotted_discount[0]), str(allotted_discount[1])],
            "allotted_interest_range": [str(allotted_interest[0]), str(allotted_interest[1])],
        }
        if isin:
            attributes["isin"] = isin.group(1)
        if total_sold is not None:
            attributes["total_sold_m_ghs"] = total_sold
        _emit_security(
            result,
            stem="GHS.AUCTION.BOG",
            tenor=tenor,
            as_of=held_on,
            attributes=attributes,
            wavg_discount=singles[0],
            wavg_interest=singles[1],
        )
    if not result.observations:
        result.errors.append("no per-security clearing rows parsed from BoG auction PDF")
    return result
