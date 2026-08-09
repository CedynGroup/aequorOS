"""GSS statsbank PxWeb parser (fixtures README §5) — the closest thing to a
real API in the Ghana stack, and roughly two years stale.

One capture is the ``POST .../interest.px`` JSON response (all six Rate
series). Quirks encoded from the harvest: the response may start with a
UTF-8 BOM (decoded with ``utf-8-sig`` either way); missing observations are
``".."`` cells or simply absent; months are ``YYYYMmm`` and map to
first-of-month dates; the table was last updated 2024-09-16 and its Month
dimension ends 2024M07 — the staleness layer flags that honestly rather
than hiding it (README quirk 5: a stale monthly source must never shadow a
live daily one).

Series ownership: GRR / IWAL / average-lending get the canonical desk codes
(this is their primary machine-readable source — BoG publishes no GRR
notice); the three series BoG tables own outright (MPR, 91-day bill,
savings) get ``GHS.GSS.*`` corroboration codes so cross-source
reconciliation can compare without supersession ping-pong on a shared code.
"""

from __future__ import annotations

import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation

from app.services.market_desk.sources.core import (
    ObservationDraft,
    ParseContext,
    ParseResult,
)

_MONTH_KEY_RE = re.compile(r"^(\d{4})M(\d{2})$")

# PxWeb value texts ARE the value codes on this deployment.
SERIES_CODES: dict[str, str] = {
    "Ghana reference rate": "GHS.GRR",
    "Interbank weighted average rate": "GHS.IWAL",
    "Average lending rate": "GHS.LENDING.AVG",
    "Monetary policy rate": "GHS.GSS.MPR",
    "Treasury bill rate (91-day)": "GHS.GSS.TBILL91",
    "Savings deposits rate": "GHS.GSS.SAVINGS",
}

_MISSING_CELLS = {"..", ".", "-", ""}


def parse_interest_px(raw: bytes, *, context: ParseContext) -> ParseResult:
    """PxWeb data response -> monthly observations at first-of-month."""
    del context
    result = ParseResult()
    payload = json.loads(raw.decode("utf-8-sig"))
    cells = payload.get("data")
    if not isinstance(cells, list):
        result.errors.append("PxWeb response has no data array")
        return result

    missing = 0
    unknown_rates: set[str] = set()
    for cell in cells:
        key = cell.get("key", [])
        values = cell.get("values", [])
        if len(key) != 2 or len(values) != 1:
            result.warnings.append(f"malformed PxWeb cell skipped: {cell!r}")
            continue
        month_key, rate_name = key
        month_match = _MONTH_KEY_RE.match(month_key)
        if month_match is None:
            result.warnings.append(f"unparseable month key skipped: {month_key!r}")
            continue
        text = values[0].strip()
        if text in _MISSING_CELLS:
            missing += 1
            continue
        series_code = SERIES_CODES.get(rate_name)
        if series_code is None:
            unknown_rates.add(rate_name)
            continue
        try:
            value = Decimal(text)
        except InvalidOperation:
            result.warnings.append(f"non-numeric PxWeb value for {rate_name}: {text!r}")
            continue
        result.observations.append(
            ObservationDraft(
                series_code=series_code,
                as_of_date=date(int(month_match.group(1)), int(month_match.group(2)), 1),
                value=value,
                unit="pct",
                attributes={"rate_name": rate_name, "month_key": month_key},
            )
        )
    if missing:
        result.warnings.append(f"skipped {missing} missing ('..') cell(s)")
    for rate_name in sorted(unknown_rates):
        result.warnings.append(f"unmapped PxWeb rate series skipped: {rate_name!r}")
    if not result.observations:
        result.errors.append("no observations parsed from PxWeb response")
    return result
