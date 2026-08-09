"""Parsers for the BoG wpDataTables JSON pages (fixtures README §1).

One raw capture is one admin-ajax ``get_wdtable`` response page:
``{draw, recordsTotal, recordsFiltered, data: [[...], ...]}``. The fetch
layer pages with ``recordsFiltered`` (``recordsTotal`` lies — it is the
shared underlying table); each page can be ingested independently because
observation writes supersede by (series_code, as_of_date).

Dirty-data policy (README "Data quirks the adapter must handle"):
- dates are ``"%d %b %Y"``; empty date cells are DROPPED with a warning
  (table 62 has two);
- exact duplicate rows are dropped and counted; same-date conflicts keep the
  last value, flagged ``source_conflict`` with both values in attributes
  (core.dedupe_and_resolve_conflicts);
- ID columns carry thousands separators — kept as attributes, comma-stripped;
- table 21's wide format uses ``0.00`` as its missing-month placeholder —
  zero is never a real value for these rates, so those cells are skipped
  and counted;
- table 63 is table 62 minus exactly 200bps — a derived corridor-floor
  display, deliberately NOT parsed (no independent information).
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from app.services.market_desk.sources.core import (
    QF_AS_OF_FROM_CAPTURE,
    ObservationDraft,
    ParseContext,
    ParseResult,
    slugify,
)

BOG_DATE_FORMAT = "%d %b %Y"

_DAY_BILL_RE = re.compile(r"^(\d+)\s+DAY\s+BILL$", re.IGNORECASE)
_MONTH_COLUMNS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
                  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

# Table 21 variable labels -> stable series slugs (README: 13 variables,
# data ends 2023). Labels not in this map still parse, under slugify().
_TABLE21_SLUGS = {
    "Ghana Reference Rate (%)": "GRR",
    "Monetary Policy Rate (%)": "MPR",
    "Inter-Bank Weighted Average (%)": "INTERBANK_WAVG",
    "Average Commercial Banks Lending Rate (%)": "LENDING_AVG",
    "Average Savings Deposits Rate (%)": "SAVINGS_AVG",
}


def _load_page(raw: bytes) -> list[list[str]]:
    payload = json.loads(raw.decode("utf-8-sig"))
    data = payload.get("data")
    if not isinstance(data, list):
        raise ValueError("wpDataTables page has no data array")
    return data


def _parse_bog_date(cell: str) -> date | None:
    text = cell.strip()
    if not text:
        return None
    return datetime.strptime(text, BOG_DATE_FORMAT).date()


def _decimal(cell: str) -> Decimal | None:
    text = cell.strip().replace(",", "")
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _tender_rows(
    raw: bytes,
    *,
    bill_prefix: str,
    other_prefix: str,
) -> ParseResult:
    """Shared shape of tables 2 and 3: Issue Date, Tender, Security Type,
    Discount Rate, Interest Rate. DISCOUNT and INTEREST are emitted as
    separate series codes exactly as published."""
    result = ParseResult()
    empty_dates = 0
    for row in _load_page(raw):
        issue_date = _parse_bog_date(row[0])
        if issue_date is None:
            empty_dates += 1
            continue
        tender = row[1].strip().replace(",", "")
        security = row[2].strip()
        day_bill = _DAY_BILL_RE.match(security)
        if day_bill:
            stem = f"{bill_prefix}.{int(day_bill.group(1))}"
        else:
            stem = f"{other_prefix}.{slugify(security)}"
        attributes = {"tender": tender, "security_type": security}
        for column, leg in ((3, "DISCOUNT"), (4, "INTEREST")):
            value = _decimal(row[column])
            if value is None:
                result.warnings.append(
                    f"non-numeric {leg.lower()} rate for {security} on "
                    f"{issue_date}: {row[column]!r}"
                )
                continue
            result.observations.append(
                ObservationDraft(
                    series_code=f"{stem}.{leg}",
                    as_of_date=issue_date,
                    value=value,
                    unit="pct",
                    attributes=dict(attributes),
                )
            )
    if empty_dates:
        result.warnings.append(f"skipped {empty_dates} row(s) with empty issue date")
    return result


def parse_table2_tbill_rates(raw: bytes, *, context: ParseContext) -> ParseResult:
    """GoG tender results (bills AND notes/bonds): GHS.TBILL.{n}.{leg} for
    "n DAY BILL" securities, GHS.GOG.{slug}.{leg} for notes/bonds."""
    del context
    return _tender_rows(raw, bill_prefix="GHS.TBILL", other_prefix="GHS.GOG")


def parse_table3_bog_bill_rates(raw: bytes, *, context: ParseContext) -> ParseResult:
    """BoG bill tender results: GHS.BOGBILL.{n}.{DISCOUNT,INTEREST}."""
    del context
    return _tender_rows(raw, bill_prefix="GHS.BOGBILL", other_prefix="GHS.BOG")


def _id_dated_rate(
    raw: bytes, *, series_code: str, id_field: str, date_label: str
) -> ParseResult:
    """Shared shape of tables 69/70/62: [ID, date, rate]."""
    result = ParseResult()
    empty_dates = 0
    for row in _load_page(raw):
        as_of = _parse_bog_date(row[1])
        if as_of is None:
            empty_dates += 1
            continue
        value = _decimal(row[2])
        if value is None:
            result.warnings.append(f"non-numeric rate on {as_of}: {row[2]!r}")
            continue
        result.observations.append(
            ObservationDraft(
                series_code=series_code,
                as_of_date=as_of,
                value=value,
                unit="pct",
                attributes={id_field: row[0].strip().replace(",", "")},
            )
        )
    if empty_dates:
        result.warnings.append(f"skipped {empty_dates} row(s) with empty {date_label}")
    return result


def parse_table69_interbank_daily(raw: bytes, *, context: ParseContext) -> ParseResult:
    """Daily weighted-average interbank rate -> GHS.INTERBANK.ON."""
    del context
    return _id_dated_rate(
        raw,
        series_code="GHS.INTERBANK.ON",
        id_field="daily_interest_rate_id",
        date_label="effective date",
    )


def parse_table70_interbank_weekly(raw: bytes, *, context: ParseContext) -> ParseResult:
    """Weekly average interbank rate -> GHS.INTERBANK.WAVG (as-of = week ending)."""
    del context
    result = _id_dated_rate(
        raw,
        series_code="GHS.INTERBANK.WAVG",
        id_field="avg_interest_rate_id",
        date_label="week ending",
    )
    for draft in result.observations:
        draft.attributes["period"] = "week_ending"
    return result


def parse_table62_mpr(raw: bytes, *, context: ParseContext) -> ParseResult:
    """MPC policy rate by decision date -> GHS.MPR (empty-date rows skipped)."""
    del context
    return _id_dated_rate(
        raw,
        series_code="GHS.MPR",
        id_field="mpc_rate_id",
        date_label="effective date",
    )


def parse_fx_pairs(raw: bytes, *, context: ParseContext) -> ParseResult:
    """Tables 31 (latest day) and 40 (full history) share one shape:
    Date, Currency, Currency Pair, Buying, Selling, Mid Rate
    -> GHS.FX.{PAIR}.{BUY,SELL,MID}."""
    del context
    result = ParseResult()
    empty_dates = 0
    for row in _load_page(raw):
        as_of = _parse_bog_date(row[0])
        if as_of is None:
            empty_dates += 1
            continue
        pair = row[2].strip().upper()
        currency = row[1].strip()
        for column, leg in ((3, "BUY"), (4, "SELL"), (5, "MID")):
            value = _decimal(row[column])
            if value is None:
                result.warnings.append(f"non-numeric {leg} for {pair} on {as_of}: {row[column]!r}")
                continue
            result.observations.append(
                ObservationDraft(
                    series_code=f"GHS.FX.{pair}.{leg}",
                    as_of_date=as_of,
                    value=value,
                    unit="rate",
                    attributes={"currency": currency},
                )
            )
    if empty_dates:
        result.warnings.append(f"skipped {empty_dates} row(s) with empty date")
    return result


_REF_BANNER_RE = re.compile(r"Weighted\s+Median\s+Rate:\s*([\d.,]+)")


def parse_table32_fx_reference(raw: bytes, *, context: ParseContext) -> ParseResult:
    """The USD/GHS weighted-median banner (README: "Parse with a regex, not
    as a table") -> GHS.FX.USDGHS.REF. The cell carries no date, so the
    as-of comes from the capture and is flagged as such."""
    result = ParseResult()
    for row in _load_page(raw):
        text = " ".join(cell for cell in row if isinstance(cell, str))
        match = _REF_BANNER_RE.search(text)
        if not match:
            continue
        value = _decimal(match.group(1))
        if value is None:
            result.errors.append(f"unparseable weighted-median banner: {text!r}")
            continue
        result.observations.append(
            ObservationDraft(
                series_code="GHS.FX.USDGHS.REF",
                as_of_date=context.as_of_date,
                value=value,
                unit="rate",
                attributes={"banner_text": text.strip()},
                quality_flags=[QF_AS_OF_FROM_CAPTURE],
            )
        )
    if not result.observations and not result.errors:
        result.errors.append("no weighted-median banner found in table 32 page")
    return result


def parse_table21_monthly_matrix(raw: bytes, *, context: ParseContext) -> ParseResult:
    """Monthly interest-rate matrix (Year, Variable, Jan..Dec) ->
    GHS.ECONDATA.{SLUG} at first-of-month. ``0.00`` is the table's
    missing-month placeholder (zero is never a real value for these rates)
    and the ``Year=0`` group is junk — both skipped with counts."""
    del context
    result = ParseResult()
    zero_cells = 0
    junk_years = 0
    for row in _load_page(raw):
        year_text = row[0].strip()
        if not year_text.isdigit() or int(year_text) < 1900:
            junk_years += 1
            continue
        year = int(year_text)
        label = row[1].strip()
        slug = _TABLE21_SLUGS.get(label, slugify(label))
        for month_index in range(12):
            value = _decimal(row[2 + month_index])
            if value is None:
                continue
            if value == 0:
                zero_cells += 1
                continue
            result.observations.append(
                ObservationDraft(
                    series_code=f"GHS.ECONDATA.{slug}",
                    as_of_date=date(year, month_index + 1, 1),
                    value=value,
                    unit="pct",
                    attributes={"variable": label, "month": _MONTH_COLUMNS[month_index]},
                )
            )
    if zero_cells:
        result.warnings.append(f"skipped {zero_cells} 0.00-as-missing cell(s)")
    if junk_years:
        result.warnings.append(f"skipped {junk_years} row(s) in junk year group(s)")
    return result
