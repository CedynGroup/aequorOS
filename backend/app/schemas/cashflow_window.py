from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CashflowWindowMonthRead(ClosedModel):
    """One calendar month × currency cell of the contractual flow window.

    ``month`` is the first day of the calendar month. Every month of the
    requested window appears for each currency that has at least one flow
    inside the window (zero months included, so charts and tables stay
    contiguous); currencies with no flows in the window are omitted entirely.
    Amounts are in the position's own currency — never converted.
    """

    month: date
    currency: str
    inflows: Decimal
    outflows: Decimal
    net: Decimal


class CashflowWindowCurrencyTotalRead(ClosedModel):
    """Whole-window totals for one currency, in that currency."""

    currency: str
    inflows: Decimal
    outflows: Decimal
    net: Decimal
    position_count: int


class CashflowWindowOverallRead(ClosedModel):
    """Whole-window totals in base-currency equivalents.

    Uses only the INGESTED base-currency leg each snapshot already carries
    (``attributes.balance_ghs`` — the same seam ``regulatory_liquidity`` and
    ``fact_derivation`` read); base-currency positions fall back to their own
    balance. Positions with no ingested conversion are excluded and counted in
    ``unconverted_count`` — never converted at an invented rate.
    """

    currency: str
    inflows: Decimal
    outflows: Decimal
    net: Decimal
    unconverted_count: int


class CashflowWindowRead(ClosedModel):
    """Contractual principal flows maturing inside [start_date, end_date].

    ``no_maturity_count`` is the honesty stat: in-scope positions of the
    current book whose snapshot carries no contractual maturity — they can
    never appear in any window and are reported, not silently dropped. It is a
    property of the book, not of the window.
    """

    bank_id: str
    start_date: date
    end_date: date
    months: list[CashflowWindowMonthRead]
    totals: list[CashflowWindowCurrencyTotalRead]
    overall: CashflowWindowOverallRead
    position_count: int
    no_maturity_count: int
