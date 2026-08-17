"""BSD1 (Liquidity Reserve Return) resolvers — the weekly daily-balance grid.

The official ``BSD1`` sheet is a THURS…WED grid: every leaf row carries seven
daily balances (columns ``B``…``H``); TOTAL/AVERAGE, sub-totals, the reserve
ratios and the required-reserve lines are the template's own formulas. The
DEPOSITS block reports the PREVIOUS week (``B7 = B3-13`` in the template), the
LIQUID ASSETS block the CURRENT week (``B28 = B3-6``), with the PERIOD date
(``B3``) as the week's Wednesday.

Daily source. The platform's daily state is the canonical position ladder:
``canonical_position_snapshots`` carry one immutable snapshot per position per
business ``as_of_date`` when a bank ingests daily (core-banking adapters,
API push). :func:`_daily` therefore aggregates snapshots whose ``as_of_date``
is EXACTLY the day of the column — never the latest-on-or-before rung — so a
month-end-only book yields ``input_required`` for six of the seven days instead
of one balance silently copied across the week (that would fabricate a daily
average). A rung is scoped to the POPULATION a line reads (its
``position_types``): a nightly push that carries only the liquid-asset book
(securities, placements, cash) is a rung for those rows and none for the
deposit / loan rows, which stay ``input_required`` rather than reading a
fabricated ``0`` (data-gap closure, 2026-08-16). On the reporting date itself
(the only day the monthly fact spine also describes) a row may name a ``facts``
fallback: the platform's period-end ``bank_facts`` fill that one column when
the ladder has no rung for it.

Ghana branches only (Guide, General Notes §1): positions carry
``attributes.branch_id`` from ingestion but no branch→country link exists yet
(the Outlet register has no branch_id join), so the branch filter cannot be
applied honestly today — see the line-map doc's "Framework asks".

Nothing here computes a BoG figure by a new rule: every resolver is a filtered
Σ over canonical rows for one business date (or a same-day market-data quote).
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import case, func, or_, select

from app.models.canonical import (
    CanonicalCounterparty,
    CanonicalPosition,
    CanonicalPositionSnapshot,
    CanonicalProduct,
)
from app.models.regulatory import BankFinancialFact
from app.services import market_data_sources

from ..sources import ResolveContext, resolver

#: Column key → days BEFORE the week's Wednesday (the PERIOD / reporting date).
#: The template's own header formulas fix this: ``B28 = B3-6`` (THURS) … ``H28 = B3``.
DAY_COLUMNS: dict[str, int] = {
    "thu": 6,
    "fri": 5,
    "sat": 4,
    "sun": 3,
    "mon": 2,
    "tue": 1,
    "wed": 0,
}
#: The DEPOSITS block reports the previous week (``B7 = B3-13``).
PREVIOUS_WEEK_SHIFT = 7

_ZERO = Decimal(0)


def target_date(rc: ResolveContext, params: dict[str, Any]) -> date | None:
    """The business date a cell describes.

    ``days_before`` (explicit) wins; otherwise the column key names the weekday
    and ``week`` ("current" | "previous") selects the block. ``None`` when the
    column is not a weekday and no explicit offset was given.
    """
    if "days_before" in params:
        return rc.period.period_end - timedelta(days=int(params["days_before"]))
    offset = DAY_COLUMNS.get(rc.column)
    if offset is None:
        return None
    if params.get("week", "current") == "previous":
        offset += PREVIOUS_WEEK_SHIFT
    return rc.period.period_end - timedelta(days=offset)


def _ladder_has_rung(
    rc: ResolveContext, day: date, position_types: Iterable[str] | None = None
) -> bool:
    """True when at least one current-generation snapshot exists for ``day``
    in the population the line reads.

    The rung is scoped to the line's ``position_types``: a bank whose daily
    push carries only part of its book (the liquid-asset ladder — securities,
    placements, cash — pushed nightly while the deposit and loan sub-ledgers
    arrive at month-end) has a same-day rung for the securities rows and NONE
    for the deposit rows, which therefore stay ``input_required`` instead of
    reading a fabricated ``0``. A line with no type filter needs any snapshot
    that day.
    """
    types = tuple(sorted(position_types)) if position_types else ()
    key = f"bsd1:rung:{day.isoformat()}:{','.join(types)}"
    cached = rc.cache.get(key)
    if cached is not None:
        return bool(cached)
    stmt = (
        select(func.count())
        .select_from(CanonicalPositionSnapshot)
        .where(
            CanonicalPositionSnapshot.organization_id == rc.ctx.organization_id,
            CanonicalPositionSnapshot.bank_id == rc.bank.id,
            CanonicalPositionSnapshot.as_of_date == day,
            CanonicalPositionSnapshot.superseded_by.is_(None),
        )
    )
    if types:
        stmt = stmt.join(
            CanonicalPosition, CanonicalPosition.id == CanonicalPositionSnapshot.position_id
        ).where(
            CanonicalPosition.superseded_by.is_(None),
            CanonicalPosition.position_type.in_(list(types)),
        )
    exists = rc.db.scalar(stmt)
    rc.cache[key] = bool(exists)
    return bool(exists)


def _attribute_equals(key: str, value: Any) -> Any:
    """``snapshot.attributes[key] == value`` regardless of whether the source
    encoded a number as a JSON number or a JSON string (SQLite's JSON_EXTRACT is
    typed; Postgres ``->>`` is text) — ``tenor_days: 91`` and ``"91"`` both match."""
    attribute = CanonicalPositionSnapshot.attributes[key]
    text_match = attribute.as_string() == str(value)
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return text_match
    return or_(text_match, attribute.as_float() == float(value))


def _cedi_measure(rc: ResolveContext, params: dict[str, Any]) -> Any:
    """Balance in BASE units: the raw balance for base-currency positions, the
    ingested ``attributes.balance_ghs`` for foreign-currency ones (the same
    convention fact derivation applies). ``measure="native"`` returns the raw
    balance in the position's own currency (Annex 1 balances by currency)."""
    if params.get("measure") == "native":
        return CanonicalPositionSnapshot.balance
    converted = func.coalesce(
        CanonicalPositionSnapshot.attributes["balance_ghs"].as_numeric(28, 6), _ZERO
    )
    return case(
        (CanonicalPosition.currency == rc.bank.currency, CanonicalPositionSnapshot.balance),
        else_=converted,
    )


def _ladder_sum(rc: ResolveContext, params: dict[str, Any], day: date) -> Decimal:  # noqa: PLR0912
    stmt = (
        select(func.coalesce(func.sum(_cedi_measure(rc, params)), 0))
        .select_from(CanonicalPositionSnapshot)
        .join(CanonicalPosition, CanonicalPosition.id == CanonicalPositionSnapshot.position_id)
        .where(
            CanonicalPositionSnapshot.organization_id == rc.ctx.organization_id,
            CanonicalPositionSnapshot.bank_id == rc.bank.id,
            CanonicalPositionSnapshot.as_of_date == day,
            CanonicalPositionSnapshot.superseded_by.is_(None),
            CanonicalPosition.superseded_by.is_(None),
        )
    )
    if types := params.get("position_types"):
        stmt = stmt.where(CanonicalPosition.position_type.in_(list(types)))
    if params.get("no_counterparty"):
        # vault cash: notes and coins have no obligor (nostro / BoG balances do)
        stmt = stmt.where(CanonicalPositionSnapshot.counterparty_id.is_(None))
    if params.get("counterparty_types") is not None or params.get("resident") is not None:
        stmt = stmt.join(
            CanonicalCounterparty,
            CanonicalCounterparty.id == CanonicalPositionSnapshot.counterparty_id,
        )
        if cpt := params.get("counterparty_types"):
            stmt = stmt.where(CanonicalCounterparty.counterparty_type.in_(list(cpt)))
        if params.get("resident") is not None:
            stmt = stmt.where(CanonicalCounterparty.resident.is_(bool(params["resident"])))
    if params.get("regulatory_categories"):
        stmt = stmt.join(
            CanonicalProduct, CanonicalProduct.id == CanonicalPositionSnapshot.product_id
        ).where(CanonicalProduct.regulatory_category.in_(list(params["regulatory_categories"])))
    if dats := params.get("deposit_account_types"):
        stmt = stmt.where(CanonicalPositionSnapshot.deposit_account_type.in_(list(dats)))
    if rate_types := params.get("rate_types"):
        stmt = stmt.where(CanonicalPositionSnapshot.rate_type.in_(list(rate_types)))
    for key, value in (params.get("attribute_eq") or {}).items():
        stmt = stmt.where(_attribute_equals(key, value))
    for key, values in (params.get("attribute_in") or {}).items():
        stmt = stmt.where(or_(*(_attribute_equals(key, v) for v in values)))
    currency = params.get("currency", "all")
    if currency == "GHS":
        stmt = stmt.where(CanonicalPosition.currency == rc.bank.currency)
    elif currency == "FX":
        stmt = stmt.where(CanonicalPosition.currency != rc.bank.currency)
    elif currency != "all":
        stmt = stmt.where(CanonicalPosition.currency == str(currency))
    if excluded := params.get("currencies_not_in"):
        stmt = stmt.where(CanonicalPosition.currency.not_in(list(excluded)))
    value = rc.db.scalar(stmt)
    return Decimal(str(value or 0))


def _facts_sum(rc: ResolveContext, spec: dict[str, Any]) -> Decimal:
    stmt = select(func.coalesce(func.sum(BankFinancialFact.amount), 0)).where(
        BankFinancialFact.organization_id == rc.ctx.organization_id,
        BankFinancialFact.bank_id == rc.bank.id,
        BankFinancialFact.reporting_period_id == rc.period.id,
        BankFinancialFact.fact_group == spec["group"],
    )
    if categories := spec.get("categories"):
        stmt = stmt.where(BankFinancialFact.category.in_(list(categories)))
    currency = spec.get("currency", "all")
    if currency == "GHS":
        stmt = stmt.where(BankFinancialFact.currency == rc.bank.currency)
    elif currency == "FX":
        stmt = stmt.where(BankFinancialFact.currency != rc.bank.currency)
    value = rc.db.scalar(stmt)
    return Decimal(str(value or 0))


@resolver("bsd1.daily")
def _daily(rc: ResolveContext, params: dict[str, Any]) -> Decimal | None:
    """Σ balance (base units) of the canonical positions matching the filters
    as of the cell's business date.

    Params: ``week`` ("current" default | "previous"), ``days_before`` (explicit
    override), the ``positions.sum`` filter vocabulary (``position_types``,
    ``counterparty_types``, ``resident``, ``regulatory_categories``,
    ``attribute_eq``) plus ``no_counterparty``, ``attribute_in``, ``deposit_account_types``,
    ``rate_types``, ``currency`` ("GHS" | "FX" | "all" | ISO code),
    ``currencies_not_in``, ``measure`` ("cedi" default | "native"),
    ``week_change`` (Σ day − Σ day−7), ``facts`` (period-end fallback spec for
    the reporting date: ``{"group", "categories", "currency"}``), ``sign``.

    Returns ``None`` (⇒ ``input_required``) when the ladder holds NO snapshot for
    that date in the line's population (``position_types``) — the balance is
    unknown, not zero.
    """
    day = target_date(rc, params)
    if day is None:
        return None
    sign = Decimal(str(params.get("sign", 1)))
    population = params.get("position_types")
    if _ladder_has_rung(rc, day, population):
        value = _ladder_sum(rc, params, day)
        if params.get("week_change"):
            prior = day - timedelta(days=PREVIOUS_WEEK_SHIFT)
            if not _ladder_has_rung(rc, prior, population):
                return None
            value -= _ladder_sum(rc, params, prior)
        return value * sign
    facts_spec = params.get("facts")
    if facts_spec and day == rc.period.period_end and not params.get("week_change"):
        return _facts_sum(rc, facts_spec) * sign
    return None


@resolver("bsd1.fx_spot")
def _fx_spot(rc: ResolveContext, params: dict[str, Any]) -> Decimal | None:
    """The bank's authoritative spot (base-currency units per 1 unit of
    ``currency``) observed ON the cell's business date — the same market-data
    arbitration fact derivation consumes, but strictly same-day: a quote is
    never carried forward into a BoG return, so non-trading days stay
    ``input_required`` unless the market-data ladder holds an observation."""
    day = target_date(rc, params)
    currency = params.get("currency")
    if day is None or not currency:
        return None
    view = market_data_sources.preferred_fx_spot(
        rc.db, rc.ctx.organization_id, rc.bank.id, str(currency), rc.bank.currency, day
    )
    if view is None or view.as_of_date != day:
        return None
    return Decimal(str(view.rate))
