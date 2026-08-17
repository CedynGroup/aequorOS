"""BSD6 resolvers — residual-maturity ladder over the BSD2 line map.

One resolver, ``bsd6.bucket``, feeds every input cell of ``BSD6A``/``BSD6B``.
It is column-driven: the line map binds a whole row (FROM BSD2 · Total · the
eight maturity bands) to one declaration and the resolver reads the band from
``rc.column``:

``from_bsd2``
    the corresponding BSD2 cell(s) of the same reporting date (the Guide's
    "FROM BSD2" column) — resolved from the computed BSD2 dependency;
``total``
    Σ over the row's *components* — the SAME resolver + params BSD2 uses for the
    lines this row aggregates (imported from BSD2's line map, never retyped),
    so the Total column reconciles to BSD2 by construction;
``overdue`` … ``5y_plus``
    the Total split by residual maturity per the Guide's BSD6 notes.

Bucket boundaries are the sheet's own column headers — Overdue · Less than 1
month · 1 month–<3 months · 3–<6 months · 6 months–<1 year · 1–<3 years ·
3–<5 years · 5 years and over — measured on a **calendar-month basis from the
reporting date** (Guide, BSD6 "Maturity"): the Guide's worked table (31 Mar →
"less than 1 month" = 1–29 Apr, "1 month–<3 months" = 30 Apr–29 Jun …) is
month-end arithmetic with the boundary date belonging to the LATER band, which
:func:`_add_months` reproduces exactly. Maturities on a Saturday/Sunday roll to
the following Monday (Guide: "succeeding business day"; public holidays are
not modelled).

Placement rules — all from the Guide's BSD6 notes, none invented here:

* assets whose due date has passed by ≥ 14 days → **Overdue**; passed by < 14
  days → less than 1 month; liabilities already due → less than 1 month
  (earliest repayment date);
* deposits → earliest repayment date; demand/savings deposits without a
  contractual date use the enriched behavioural maturity when present
  (Guide 15 & 16: historical withdrawal pattern), otherwise "less than 1
  month" (on demand);
* provisions for bad debts and interest in suspense → **Overdue**;
* cash on hand and sight balances → **Overdue**; reserves → **5 years and
  over** (negative → Overdue) — applied to statutory reserve balances with the
  central bank AND to shareholders' reserves / paid-up capital (perpetual,
  undated non-demand items the platform's liquidity ladder already places in
  the longest band; see docs/bog_returns/bsd6_line_map.md "Interpretations");
* other undated positions follow the platform's ladder convention
  (``regulatory_liquidity._ladder_bucket_index``): demand-natured (cash; call
  money) → shortest band, everything else → longest band;
* a fact-sourced component with no Guide placement (e.g. Other assets) leaves
  the row's band cells blank (``input_required``) — the Total and FROM BSD2
  cells still fill; the bank allocates.

Read-only over canonical facts/positions; the position query replicates
``sources.positions.sum``'s filters one-for-one so a BSD6 Total equals the
BSD2 cell it mirrors.
"""

from __future__ import annotations

import json
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select

from app.models.canonical import (
    CanonicalCounterparty,
    CanonicalPosition,
    CanonicalPositionSnapshot,
    CanonicalProduct,
)

from ..sources import ResolveContext, get_resolver, resolver

#: Band keys in template column order (D … K); the line map binds them to the
#: sheet's bucket columns and the resolver reads them from ``rc.column``.
BUCKETS: tuple[str, ...] = (
    "overdue",
    "lt_1m",
    "1m_lt_3m",
    "3m_lt_6m",
    "6m_lt_1y",
    "1y_lt_3y",
    "3y_lt_5y",
    "5y_plus",
)
#: Months to the LOWER boundary of each dated band after "lt_1m" (Guide table).
_BAND_MONTHS: tuple[int, ...] = (1, 3, 6, 12, 36, 60)
FROM_BSD2 = "from_bsd2"
TOTAL = "total"
#: Guide, BSD6 "Overdue": due date passed by fourteen days or more.
_OVERDUE_DAYS = 14
_DEMAND_DEPOSITS = frozenset({"CURRENT", "CALL", "SAVINGS"})
_SATURDAY = 5

_ZERO = Decimal(0)


# ---------------------------------------------------------------------------
# calendar arithmetic (Guide, BSD6 "Maturity")
# ---------------------------------------------------------------------------


def _add_months(anchor: date, months: int) -> date:
    """``anchor`` + ``months`` calendar months; a month-end anchor lands on the
    target month's end (31 Mar + 1 → 30 Apr; 30 Jun + 1 → 31 Jul), otherwise
    the day is kept (clamped to the target month's length)."""
    year = anchor.year + (anchor.month - 1 + months) // 12
    month = (anchor.month - 1 + months) % 12 + 1
    last_day = monthrange(year, month)[1]
    if anchor.day == monthrange(anchor.year, anchor.month)[1]:
        return date(year, month, last_day)
    return date(year, month, min(anchor.day, last_day))


def band_boundaries(period_end: date) -> tuple[date, ...]:
    """Lower boundary of each dated band after "less than 1 month" — the date
    from which "1 month–<3 months", "3–<6 months", … begin (inclusive)."""
    return tuple(_add_months(period_end, months) for months in _BAND_MONTHS)


def _next_business_day(day: date) -> date:
    """Guide: a maturity on a non-business day counts as the succeeding
    business day (weekends only — no holiday calendar is modelled)."""
    if day.weekday() >= _SATURDAY:
        return day + timedelta(days=7 - day.weekday())
    return day


def bucket_for(maturity: date, period_end: date, *, side: str) -> str:
    """The template band a dated item falls in, relative to ``period_end``."""
    due = _next_business_day(maturity)
    if due <= period_end:
        if side == "asset" and (period_end - due).days >= _OVERDUE_DAYS:
            return "overdue"
        return "lt_1m"
    band = "lt_1m"
    for lower, next_band in zip(band_boundaries(period_end), BUCKETS[2:], strict=True):
        if due < lower:
            return band
        band = next_band
    return band


# ---------------------------------------------------------------------------
# position rows (filters replicate sources.positions.sum one-for-one)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _PositionRow:
    amount: Decimal
    position_type: str
    contractual_maturity: date | None
    behavioral_maturity_months: int | None
    deposit_account_type: str | None
    attributes: dict[str, Any]


def _position_rows(  # noqa: PLR0912 — one branch per positions.sum filter
    rc: ResolveContext, params: dict[str, Any], *, bsd2_column: str
) -> list[_PositionRow]:
    measure = (
        CanonicalPositionSnapshot.notional
        if params.get("measure") == "notional"
        else CanonicalPositionSnapshot.balance
    )
    latest = (
        select(
            CanonicalPositionSnapshot.position_id.label("pid"),
            func.max(CanonicalPositionSnapshot.as_of_date).label("as_of"),
        )
        .where(
            CanonicalPositionSnapshot.organization_id == rc.ctx.organization_id,
            CanonicalPositionSnapshot.bank_id == rc.bank.id,
            CanonicalPositionSnapshot.as_of_date <= rc.period.period_end,
            CanonicalPositionSnapshot.superseded_by.is_(None),
        )
        .group_by(CanonicalPositionSnapshot.position_id)
        .subquery()
    )
    stmt = (
        select(
            measure,
            CanonicalPosition.position_type,
            CanonicalPositionSnapshot.contractual_maturity,
            CanonicalPositionSnapshot.behavioral_maturity_months,
            CanonicalPositionSnapshot.deposit_account_type,
            CanonicalPositionSnapshot.attributes,
        )
        .select_from(CanonicalPositionSnapshot)
        .join(
            latest,
            (latest.c.pid == CanonicalPositionSnapshot.position_id)
            & (latest.c.as_of == CanonicalPositionSnapshot.as_of_date),
        )
        .join(CanonicalPosition, CanonicalPosition.id == CanonicalPositionSnapshot.position_id)
        .where(
            CanonicalPositionSnapshot.organization_id == rc.ctx.organization_id,
            CanonicalPositionSnapshot.bank_id == rc.bank.id,
            CanonicalPositionSnapshot.superseded_by.is_(None),
            CanonicalPosition.superseded_by.is_(None),
        )
    )
    if types := params.get("position_types"):
        stmt = stmt.where(CanonicalPosition.position_type.in_(list(types)))
    if (
        params.get("counterparty_types") is not None
        or params.get("resident") is not None
        or params.get("country_codes") is not None
    ):
        stmt = stmt.join(
            CanonicalCounterparty,
            CanonicalCounterparty.id == CanonicalPositionSnapshot.counterparty_id,
        )
        if cpt := params.get("counterparty_types"):
            stmt = stmt.where(CanonicalCounterparty.counterparty_type.in_(list(cpt)))
        if params.get("resident") is not None:
            stmt = stmt.where(CanonicalCounterparty.resident.is_(bool(params["resident"])))
        if countries := params.get("country_codes"):
            stmt = stmt.where(CanonicalCounterparty.country_code.in_(list(countries)))
    if params.get("regulatory_categories") or params.get("product_codes"):
        stmt = stmt.join(
            CanonicalProduct, CanonicalProduct.id == CanonicalPositionSnapshot.product_id
        )
        if cats := params.get("regulatory_categories"):
            stmt = stmt.where(CanonicalProduct.regulatory_category.in_(list(cats)))
        if codes := params.get("product_codes"):
            stmt = stmt.where(CanonicalProduct.product_code.in_(list(codes)))
    if params.get("encumbered") is not None:
        stmt = stmt.where(CanonicalPositionSnapshot.encumbered.is_(bool(params["encumbered"])))
    for key, value in (params.get("attribute_eq") or {}).items():
        stmt = stmt.where(CanonicalPositionSnapshot.attributes[key].as_string() == str(value))
    # Currency: the explicit override BSD2 declared, else the BSD2 column rule
    # (BSD6A mirrors BSD2's Domestic column, BSD6B its Foreign column).
    currency = params.get("currency")
    if currency == "GHS" or (currency is None and bsd2_column == "domestic"):
        stmt = stmt.where(CanonicalPosition.currency == rc.bank.currency)
    elif currency == "FX" or (currency is None and bsd2_column == "foreign"):
        stmt = stmt.where(CanonicalPosition.currency != rc.bank.currency)
    sign = Decimal(str(params.get("sign", 1)))
    rows: list[_PositionRow] = []
    for amount, ptype, maturity, behavioural, account_type, attributes in rc.db.execute(stmt):
        rows.append(
            _PositionRow(
                amount=Decimal(str(amount or 0)) * sign,
                position_type=str(ptype),
                contractual_maturity=maturity,
                behavioral_maturity_months=behavioural,
                deposit_account_type=account_type,
                attributes=dict(attributes or {}),
            )
        )
    return rows


def _undated_bucket(row: _PositionRow, period_end: date, *, side: str) -> str:
    """Placement of a position with no contractual maturity."""
    if row.position_type == "CASH":
        return "overdue"  # Guide note: cash on hand / sight balances → Overdue
    if row.position_type == "DEPOSIT":
        if row.behavioral_maturity_months is not None and (
            (row.deposit_account_type or "").upper() in _DEMAND_DEPOSITS
            or row.deposit_account_type is None
        ):
            return bucket_for(
                _add_months(period_end, int(row.behavioral_maturity_months)),
                period_end,
                side=side,
            )
        return "lt_1m"  # Guide: deposits at their earliest repayment date (on demand)
    if str(row.attributes.get("tenor", "")).lower() == "call":
        return "lt_1m"  # money at call: demand-natured (platform ladder convention)
    return BUCKETS[-1]  # other undated → longest band (platform ladder convention)


def _bucketize(rows: list[_PositionRow], period_end: date, *, side: str) -> dict[str, Decimal]:
    totals: dict[str, Decimal] = dict.fromkeys(BUCKETS, _ZERO)
    for row in rows:
        if row.contractual_maturity is not None:
            band = bucket_for(row.contractual_maturity, period_end, side=side)
        else:
            band = _undated_bucket(row, period_end, side=side)
        totals[band] += row.amount
    return totals


# ---------------------------------------------------------------------------
# row computation (memoised per row across the ten bound columns)
# ---------------------------------------------------------------------------


@dataclass
class _RowValues:
    total: Decimal
    buckets: dict[str, Decimal]
    #: True when a fact-sourced component has no Guide placement — the band
    #: cells stay blank for the bank to allocate.
    unallocated: bool
    #: False when every component resolved to None (its BSD2 line is
    #: input_required — e.g. a reference dataset never ingested): the BSD6 row
    #: stays input_required too, never a silent 0.
    resolved: bool = True


def _bsd2_context(rc: ResolveContext, bsd2_column: str) -> ResolveContext:
    return ResolveContext(
        db=rc.db,
        ctx=rc.ctx,
        bank=rc.bank,
        period=rc.period,
        column=bsd2_column,
        dependencies=rc.dependencies,
        cache=rc.cache,
    )


def _compute_row(rc: ResolveContext, params: dict[str, Any]) -> _RowValues:
    side = str(params.get("side", "asset"))
    bsd2_column = str(params.get("bsd2_column", "domestic"))
    period_end = rc.period.period_end
    total = _ZERO
    buckets: dict[str, Decimal] = dict.fromkeys(BUCKETS, _ZERO)
    unallocated = False
    resolved = False
    for component in params.get("components", ()):
        source = str(component["source"])
        cparams: dict[str, Any] = dict(component.get("params") or {})
        if source == "positions.sum":
            rows = _position_rows(rc, cparams, bsd2_column=bsd2_column)
            split = _bucketize(rows, period_end, side=side)
            for name, value in split.items():
                buckets[name] += value
            total += sum(split.values(), _ZERO)
            resolved = True
            continue
        raw = get_resolver(source)(_bsd2_context(rc, bsd2_column), cparams)
        if raw is None:
            continue  # the BSD2 leaf is input_required: nothing to place, nothing to blank
        resolved = True
        amount = Decimal(str(raw))
        total += amount
        band = component.get("bucket")
        if band is None:
            # no Guide placement for this component (fact residual, accruals sub-ledger):
            # a non-zero amount leaves the bands to the bank; a zero has nothing to place
            unallocated = unallocated or amount != 0
            continue
        if amount < 0 and component.get("negative_bucket"):
            band = component["negative_bucket"]
        buckets[str(band)] += amount
    return _RowValues(total=total, buckets=buckets, unallocated=unallocated, resolved=resolved)


def _from_bsd2(rc: ResolveContext, params: dict[str, Any]) -> Decimal | None:
    dep = rc.dependencies.get("BSD2")
    if dep is None:
        return None
    values = [dep.get(("BSD2", ref)) for ref in params.get("bsd2_refs", ())]
    present = [Decimal(str(v)) for v in values if v is not None and not isinstance(v, str)]
    if not present:
        return None
    return sum(present, _ZERO)


@resolver("bsd6.bucket")
def _bsd6_bucket(rc: ResolveContext, params: dict[str, Any]) -> Decimal | None:
    """Column-driven BSD6 row source (see module docstring).

    ``params``: ``side`` (asset|liability), ``bsd2_column`` (domestic|foreign),
    ``bsd2_refs`` (BSD2 cells the FROM BSD2 column reads), ``components`` (the
    BSD2 leaf sources this row aggregates: ``{"source", "params", "bucket",
    "negative_bucket"}``).
    """
    key = "bsd6:" + json.dumps(params, sort_keys=True, default=str)
    values: _RowValues | None = rc.cache.get(key)
    if values is None:
        values = _compute_row(rc, params)
        rc.cache[key] = values
    if not values.resolved:
        # every BSD2 leaf behind the row is input_required (e.g. the accruals
        # sub-ledger not ingested): the whole BSD6 row stays input_required — the
        # FROM BSD2 subtotal would only echo BSD2's formula over blanks
        return None
    if rc.column == FROM_BSD2:
        return _from_bsd2(rc, params)
    if rc.column == TOTAL:
        return values.total
    if rc.column in BUCKETS:
        if values.unallocated:
            return None
        return values.buckets[rc.column]
    return None
