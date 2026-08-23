"""BSD14 resolvers — the weekly return on interest & lending rates.

FORM BSD14 (``INTEREST&LENDING-RATES``) is one grid: a currency row (Cedis /
USD / GBP / DEM / All other currencies) × the rate offered on each product —
BORROWING RATES (demand deposit, savings deposit, fixed/time deposits by tenor
1·2·3·6·12·24·36 months, certificates of deposit, call deposit, any other) and
LENDING RATES by sector (agriculture, exports, mining/quarrying,
manufacturing, construction, imports, commerce, others), plus the bank's BASE
RATE. Everything is a percentage.

The platform holds no "offered rate" table. What it does hold is every
DEPOSIT / LOAN position with its contractual ``interest_rate`` (a decimal
fraction), balance, currency, ``deposit_account_type``, tenor dates and — via
the documented BSD4 convention — the borrower's ``sector`` attribute. The
single resolver here therefore reports the **balance-weighted average
contractual rate** (or the min / max) over the positions that match a product
cell, as a percentage. That is an aggregation of existing state, not a new
rule; and it is honest about its limits:

* a cell with NO matching position, or whose matches carry no
  ``interest_rate``, resolves to ``None`` ⇒ ``input_required`` ("product rate
  table required") — never 0;
* lending-rate cells resolve to ``None`` when the currency's loan book carries
  no ``sector`` attribute at all (nothing is guessed — the same rule BSD4
  applies); once the book is classified, unclassified loans fall into *Others*;
* the BASE RATE is a declared figure (not derivable from positions) and is
  ``input_required`` in the line map.

Time-deposit tenor: ``attributes.tenor_months`` when supplied; else the
original term (``contractual_maturity − origination_date``); else the
remaining term from the period end. Each deposit is assigned to the NEAREST
official tenor column (bucket edges at the midpoints 1.5 · 2.5 · 4.5 · 9 · 18 ·
30 months). Certificates of deposit are DEPOSIT positions whose
``attributes.instrument`` is ``certificate_of_deposit`` (or ``cd``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select

from app.domain.ingestion.constants import INCLUDED_VALIDATION_STATUSES
from app.models.canonical import (
    CanonicalCounterparty,
    CanonicalPosition,
    CanonicalPositionSnapshot,
)

from ..sources import ResolveContext, resolver

#: The template's named foreign-currency rows.
NAMED_CURRENCIES: tuple[str, ...] = ("USD", "GBP", "DEM")
OTHER = "other"
#: Official fixed/time-deposit tenor columns (months), template order.
TENOR_MONTHS: tuple[int, ...] = (1, 2, 3, 6, 12, 24, 36)
_HUNDRED = Decimal("100")
_ZERO = Decimal("0")
_DAYS_PER_MONTH = Decimal("365") / Decimal("12")

#: Column key → product filter. Deposit products key on the snapshot's
#: ``deposit_account_type``; time deposits add the tenor bucket; lending
#: products key on the sector group (BSD4 ``sector`` attribute convention).
COLUMN_PRODUCTS: dict[str, dict[str, Any]] = {
    "demand": {"position_types": ["DEPOSIT"], "deposit_account_types": ["CURRENT"]},
    "savings": {"position_types": ["DEPOSIT"], "deposit_account_types": ["SAVINGS"]},
    **{
        f"td_{months}": {
            "position_types": ["DEPOSIT"],
            "deposit_account_types": ["FIXED"],
            "tenor_months": months,
        }
        for months in TENOR_MONTHS
    },
    "cd": {
        "position_types": ["DEPOSIT"],
        "attribute_in": {"instrument": ["certificate_of_deposit", "cd"]},
    },
    "call": {"position_types": ["DEPOSIT"], "deposit_account_types": ["CALL"]},
    "other_deposit": {"position_types": ["DEPOSIT"], "deposit_account_types": ["OTHER"]},
    "agriculture": {"position_types": ["LOAN"], "sector": "agriculture"},
    "exports": {"position_types": ["LOAN"], "sector": "exports"},
    "mining": {"position_types": ["LOAN"], "sector": "mining"},
    "manufacturing": {"position_types": ["LOAN"], "sector": "manufacturing"},
    "construction": {"position_types": ["LOAN"], "sector": "construction"},
    "imports": {"position_types": ["LOAN"], "sector": "imports"},
    "commerce": {"position_types": ["LOAN"], "sector": "commerce"},
    "others": {"position_types": ["LOAN"], "sector": "others"},
}

#: BSD14 lending sector groups over the BSD4 ``sector`` key vocabulary
#: (``agriculture.*``, ``mining.*``, ``manufacturing.export.*`` /
#: ``manufacturing.home.*``, ``construction.*``, ``commerce.import.*`` /
#: ``commerce.export.*`` / ``commerce.*``, ``transport.*``, ``services.*``,
#: ``miscellaneous``) — plus the BSD14 column names themselves as aliases.
SECTOR_GROUPS: tuple[str, ...] = (
    "agriculture",
    "exports",
    "mining",
    "manufacturing",
    "construction",
    "imports",
    "commerce",
    "others",
)
_GROUP_ALIASES: dict[str, str] = {
    "agric": "agriculture",
    "agricultural": "agriculture",
    "export": "exports",
    "exports": "exports",
    "mining_quarrying": "mining",
    "quarrying": "mining",
    "import": "imports",
    "imports": "imports",
    "trade": "commerce",
    "other": "others",
    "others": "others",
    "miscellaneous": "others",
}


_PREFIX_GROUPS: tuple[tuple[str, str], ...] = (
    ("manufacturing.export", "exports"),
    ("commerce.export", "exports"),
    ("commerce.import", "imports"),
    ("manufacturing", "manufacturing"),
    ("commerce", "commerce"),
    ("agriculture", "agriculture"),
    ("mining", "mining"),
    ("construction", "construction"),
)


def sector_group(raw: Any) -> str | None:
    """Documented ``sector`` value → BSD14 lending column group; ``None`` when
    the value is absent (the loan is unclassified)."""
    if raw in (None, ""):
        return None
    text = str(raw).strip().lower().replace(" ", "_").replace("-", "_").replace("/", "_")
    if text in SECTOR_GROUPS:
        return text
    if text in _GROUP_ALIASES:
        return _GROUP_ALIASES[text]
    for prefix, group in _PREFIX_GROUPS:  # longest prefixes first
        if text == prefix or text.startswith(prefix + "."):
            return group
    return "others"


@dataclass(frozen=True)
class _Row:
    currency: str
    balance: Decimal
    weight: Decimal  # cedi equivalent when available (for the mixed "other" bucket)
    rate: Decimal | None  # decimal fraction
    account_type: str | None
    tenor_months: Decimal | None
    sector: str | None  # BSD14 group or None (unclassified)
    attributes: dict[str, Any]


def _tenor_months(
    attrs: dict[str, Any], origination: date | None, maturity: date | None, as_of: date
) -> Decimal | None:
    explicit = attrs.get("tenor_months")
    if explicit not in (None, ""):
        try:
            return Decimal(str(explicit))
        except ArithmeticError:
            return None
    if maturity is None:
        return None
    start = origination or as_of
    days = Decimal((maturity - start).days)
    return days / _DAYS_PER_MONTH if days > _ZERO else _ZERO


def tenor_bucket(months: Decimal | None) -> int | None:
    """Nearest official tenor column: edges at the midpoints of TENOR_MONTHS."""
    if months is None:
        return None
    best: int | None = None
    best_gap: Decimal | None = None
    for tenor in TENOR_MONTHS:
        gap = abs(months - Decimal(tenor))
        if best_gap is None or gap < best_gap:
            best, best_gap = tenor, gap
    return best


def _book(rc: ResolveContext, position_type: str) -> tuple[_Row, ...]:
    """The current generation of ``position_type`` positions (latest snapshot
    on/before period end) with the fields the rate cells need."""
    key = f"bsd14:book:{position_type}"
    if key in rc.cache:
        return rc.cache[key]
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
            CanonicalPositionSnapshot.withdrawn_at.is_(None),
            CanonicalPositionSnapshot.validation_status.in_(INCLUDED_VALIDATION_STATUSES),
        )
        .group_by(CanonicalPositionSnapshot.position_id)
        .subquery()
    )
    stmt = (
        select(
            CanonicalPosition.currency,
            CanonicalPosition.origination_date,
            CanonicalPositionSnapshot.balance,
            CanonicalPositionSnapshot.interest_rate,
            CanonicalPositionSnapshot.deposit_account_type,
            CanonicalPositionSnapshot.contractual_maturity,
            CanonicalPositionSnapshot.attributes,
            CanonicalCounterparty.attributes,
        )
        .select_from(CanonicalPositionSnapshot)
        .join(
            latest,
            (latest.c.pid == CanonicalPositionSnapshot.position_id)
            & (latest.c.as_of == CanonicalPositionSnapshot.as_of_date),
        )
        .join(CanonicalPosition, CanonicalPosition.id == CanonicalPositionSnapshot.position_id)
        .outerjoin(
            CanonicalCounterparty,
            CanonicalCounterparty.id == CanonicalPositionSnapshot.counterparty_id,
        )
        .where(
            CanonicalPositionSnapshot.organization_id == rc.ctx.organization_id,
            CanonicalPositionSnapshot.bank_id == rc.bank.id,
            CanonicalPositionSnapshot.superseded_by.is_(None),
            CanonicalPositionSnapshot.withdrawn_at.is_(None),
            CanonicalPositionSnapshot.validation_status.in_(INCLUDED_VALIDATION_STATUSES),
            CanonicalPosition.superseded_by.is_(None),
            CanonicalPosition.withdrawn_at.is_(None),
            CanonicalPosition.position_type == position_type,
        )
    )
    rows: list[_Row] = []
    for (
        currency,
        origination,
        balance,
        rate,
        account_type,
        maturity,
        attrs,
        cp_attrs,
    ) in rc.db.execute(stmt):
        attributes = dict(attrs or {})
        cp_attributes = dict(cp_attrs or {})
        raw_sector = attributes.get("sector", cp_attributes.get("sector"))
        amount = Decimal(str(balance or 0))
        ghs = attributes.get("balance_ghs")
        weight = amount
        if ghs not in (None, ""):
            try:
                weight = Decimal(str(ghs))
            except ArithmeticError:
                weight = amount
        rows.append(
            _Row(
                currency=str(currency).upper(),
                balance=amount,
                weight=abs(weight),
                rate=Decimal(str(rate)) if rate is not None else None,
                account_type=str(account_type).upper() if account_type else None,
                tenor_months=_tenor_months(attributes, origination, maturity, rc.period.period_end),
                sector=sector_group(raw_sector),
                attributes=attributes,
            )
        )
    rc.cache[key] = tuple(rows)
    return rc.cache[key]


def _currency_matches(row: _Row, currency: str, base: str, named: tuple[str, ...]) -> bool:
    if currency == OTHER:
        return row.currency != base and row.currency not in named
    return row.currency == currency


def _matches(row: _Row, spec: dict[str, Any]) -> bool:  # noqa: PLR0911
    types = spec.get("deposit_account_types")
    if types and (row.account_type or "") not in {str(t).upper() for t in types}:
        return False
    tenor = spec.get("tenor_months")
    if tenor is not None and tenor_bucket(row.tenor_months) != int(tenor):
        return False
    for key, allowed in (spec.get("attribute_in") or {}).items():
        raw = row.attributes.get(key)
        if raw in (None, ""):
            return False
        if str(raw).strip().lower() not in {str(v).lower() for v in allowed}:
            return False
    for key, value in (spec.get("attribute_eq") or {}).items():
        if str(row.attributes.get(key)) != str(value):
            return False
    sector = spec.get("sector")
    if sector is not None:
        group = sector_group(sector)
        if group == "others":
            return row.sector in (None, "others")
        return row.sector == group
    return True


@resolver("bsd14.rate")
def _rate(rc: ResolveContext, params: dict[str, Any]) -> Decimal | None:  # noqa: PLR0911
    """The offered rate for one product cell, as a PERCENT (``unscaled``).

    Params: ``currency`` ("GHS" | "USD" | "GBP" | "DEM" | "other"; the bank's
    base code for the Cedis row), ``product`` (a :data:`COLUMN_PRODUCTS` key,
    defaults to the column key) or the explicit filters ``position_types``,
    ``deposit_account_types``, ``tenor_months``, ``attribute_in``,
    ``attribute_eq``, ``sector``; ``statistic`` ("weighted_avg" default |
    "min" | "max"); ``named`` (override of the named-currency rows).

    ``None`` when no position matches or none of the matches carries an
    ``interest_rate`` (⇒ input_required: product rate table required), and
    for sector cells when the currency's loan book is entirely unclassified.
    """
    spec: dict[str, Any] = dict(COLUMN_PRODUCTS.get(str(params.get("product") or rc.column), {}))
    spec.update({k: v for k, v in params.items() if k not in ("product", "currency", "statistic")})
    if not spec.get("position_types"):
        return None
    currency = str(params.get("currency") or rc.bank.currency)
    currency = OTHER if currency.lower() == OTHER else currency.upper()
    named = tuple(str(c).upper() for c in params.get("named") or NAMED_CURRENCIES)
    statistic = str(params.get("statistic", "weighted_avg"))

    rows: list[_Row] = []
    for position_type in spec["position_types"]:
        rows.extend(
            row
            for row in _book(rc, str(position_type))
            if _currency_matches(row, currency, rc.bank.currency, named)
        )
    if spec.get("sector") is not None and not any(row.sector is not None for row in rows):
        return None  # the loan book carries no sector classification at all
    matched = [row for row in rows if _matches(row, spec) and row.rate is not None]
    if not matched:
        return None
    rates = [row.rate for row in matched if row.rate is not None]
    if statistic == "min":
        return min(rates) * _HUNDRED
    if statistic == "max":
        return max(rates) * _HUNDRED
    weight = sum((row.weight for row in matched), _ZERO)
    if weight <= _ZERO:
        return sum(rates, _ZERO) / Decimal(len(rates)) * _HUNDRED
    return sum((row.weight * (row.rate or _ZERO) for row in matched), _ZERO) / weight * _HUNDRED


@resolver("bsd14.column_constant")
def _column_constant(rc: ResolveContext, params: dict[str, Any]) -> Any:
    """A fixed value chosen by the cell's column key: ``{"values": {"td_1": 1,
    "td_2": 2, …}}`` — the template's tenor header row (D14:J14) is the form's
    only captured input row and must export verbatim."""
    return dict(params.get("values") or {}).get(rc.column)


__all__ = [
    "COLUMN_PRODUCTS",
    "NAMED_CURRENCIES",
    "OTHER",
    "SECTOR_GROUPS",
    "TENOR_MONTHS",
    "sector_group",
    "tenor_bucket",
]
