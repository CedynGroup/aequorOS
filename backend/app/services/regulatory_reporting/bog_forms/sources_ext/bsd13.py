"""BSD13 resolvers — Net Open Position (Form FXP) by currency.

FORM BSD13 asks, per currency column (US DOLLAR / GB POUND / DEM / Other
Currencies), for the three components the FX engine already nets:

* **(A) Net Assets** — Schedule A: assets minus liabilities in the currency;
* **(B) Liabilities on contingent credits** — Schedule B (crystallised
  contingents);
* **(C) Net Trading Position** — Schedule C: spot and forward purchases minus
  sales under contracts outstanding;

and their sum, the NOP, its cedi equivalent, the AGGREGATE forex open position
(AFOP), Net Own Funds and AFOP as a % of NOF.

Nothing here computes a BoG figure by a new rule. Three read-only sources:

1. **The FX engine's latest succeeded baseline run** (``RegulatoryRun`` module
   ``fx``): ``metrics.currencies[]`` (``net_ccy``, ``net_ghs``, ``spot_ghs``
   per currency), ``nop_ghs`` (AFOP), ``tier1_ghs`` (NOF), ``nop_pct_tier1``
   and the limit parameters — exactly what the FX-NOP return, DBK-DAILY and
   BSD1B report, so BSD13 can never disagree with them.
2. **The ``fx_position`` facts** the run consumed (attributes ``assets_ccy``,
   ``liabilities_ccy``, ``net_derivatives_ccy``, ``net_ccy``, ``spot_ghs``):
   the per-currency composition the run's ``input_hash`` covers.
3. **Canonical positions** (current generation, latest snapshot on/before the
   period end): the Schedule A breakdown by nature (cash / nostro / placements
   / securities / loans / deposits / borrowings) filtered to ONE currency, and
   the FX contract book (``FX_HEDGE`` — the same rows ``fact_derivation``
   nets into ``net_derivatives_ccy``) split into spot vs forward, purchase vs
   sale, plus the per-deal forward-contract listing of Schedule C's annexure.

Conventions (documented in docs/bog_returns/bsd13_line_map.md):

* Currency columns are the template's: ``USD``, ``GBP``, ``DEM`` (bound
  literally — the template predates the euro; EUR therefore reports under
  *Other Currencies* unless a bank re-points the DEM column) and ``other`` =
  every non-base currency not in the named set, expressed in cedi equivalent.
* Contract sides follow the FX engine's own convention: a contract's
  ``balance`` is its notional in the SELL currency; the buy leg is
  ``notional × contract_rate`` (buy units per sell unit); a buy leg without a
  positive ``contract_rate`` is excluded (as the engine excludes it). Spot vs
  forward: ``attributes.settlement`` ("spot" | "forward") wins; else an
  instrument slug of spot; else settlement within two days of the as-of date
  is spot; everything else outstanding (forward / NDF / option / CCS) is
  forward at full notional — the engine's delta basis, so Schedule C's NET
  TRADING POSITION reconciles to the fact's ``net_derivatives_ccy``.
* Sales are returned NEGATIVE: the template's own formulas are
  ``Net Spot = Spot Purchase + Spot Sale`` with the caption "(L + or S −)".
* **No rate is ever assumed.** Every cedi-equivalent aggregation on this form
  (the *Other Currencies* column, everywhere it appears) goes through
  :func:`_to_report_currency`, which counts what it cannot convert, and the
  cell then refuses via :func:`_refuse_unconverted` — ``MISSING_REQUIRED_INPUT``
  naming the currencies, blank cell, reason in the completion notes. Parity
  with the reporting currency is not a fallback: it is a rate nobody governs,
  and on a filed net open position it reports the foreign amount itself
  (audit 2026-08-22 D-13 / D-21).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select

from app.domain.authority.outcomes import NotComputable, OutcomeState, outcome
from app.domain.ingestion.constants import INCLUDED_VALIDATION_STATUSES
from app.models import RegulatoryRun
from app.models.canonical import (
    CanonicalCounterparty,
    CanonicalPosition,
    CanonicalPositionSnapshot,
)
from app.models.regulatory import BankFinancialFact
from app.services import market_data_sources

from ..sources import ResolveContext, resolver

#: The template's named currency columns (in template order).
NAMED_CURRENCIES: tuple[str, ...] = ("USD", "GBP", "DEM")
OTHER = "other"
#: Column key → currency for the Domestic/Foreign-style column context.
COLUMN_CURRENCIES: dict[str, str] = {"usd": "USD", "gbp": "GBP", "dem": "DEM", "other": OTHER}

_ZERO = Decimal("0")
_SPOT_INSTRUMENTS = frozenset({"spot", "fx_spot", "spot_deal", "tod", "tom"})
_SPOT_SETTLEMENT_DAYS = 2
_MEASURE_ROW_ATTR = {
    "assets": "assets_ccy",
    "liabilities": "liabilities_ccy",
    "net_trading": "net_derivatives_ccy",
}
_BOOK_METRICS = {
    "net_worth": "tier1_ghs",
    "afop": "nop_ghs",
    "afop_pct_nof": "nop_pct_tier1",
    "aggregate_limit_pct": "nop_aggregate_limit_pct",
    "single_limit_pct": "nop_single_limit_pct",
    "sum_long_ghs": "sum_long_ghs",
    "sum_short_ghs": "sum_short_ghs",
}
_CONTRACT_MEASURES = frozenset({"spot_long", "spot_short", "forward_long", "forward_short"})


def _dec(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (ArithmeticError, ValueError):
        return None


# ---------------------------------------------------------------------------
# sources: the FX run, the fx_position facts, spots
# ---------------------------------------------------------------------------


def _fx_run(rc: ResolveContext) -> RegulatoryRun | None:
    key = "bsd13:fx_run"
    if key not in rc.cache:
        rc.cache[key] = rc.db.scalar(
            select(RegulatoryRun)
            .where(
                RegulatoryRun.organization_id == rc.ctx.organization_id,
                RegulatoryRun.bank_id == rc.bank.id,
                RegulatoryRun.reporting_period_id == rc.period.id,
                RegulatoryRun.module == "fx",
                RegulatoryRun.scenario_code == "baseline",
                RegulatoryRun.status == "succeeded",
            )
            .order_by(RegulatoryRun.created_at.desc(), RegulatoryRun.id.desc())
            .limit(1)
        )
    return rc.cache[key]


def _run_currencies(rc: ResolveContext) -> dict[str, dict[str, Any]]:
    run = _fx_run(rc)
    if run is None:
        return {}
    return {
        str(item["currency"]).upper(): dict(item)
        for item in (run.metrics or {}).get("currencies", [])
    }


def _fx_facts(rc: ResolveContext) -> dict[str, dict[str, Any]]:
    """currency → ``fx_position`` fact attributes (+ ``amount``) for the period."""
    key = "bsd13:fx_facts"
    if key not in rc.cache:
        rows = rc.db.scalars(
            select(BankFinancialFact).where(
                BankFinancialFact.organization_id == rc.ctx.organization_id,
                BankFinancialFact.bank_id == rc.bank.id,
                BankFinancialFact.reporting_period_id == rc.period.id,
                BankFinancialFact.fact_group == "fx_position",
            )
        )
        facts: dict[str, dict[str, Any]] = {}
        for fact in rows:
            attrs = dict(fact.attributes or {})
            currency = str(attrs.get("currency") or fact.category).upper()
            attrs["amount"] = str(fact.amount)
            facts[currency] = attrs
        rc.cache[key] = facts
    return rc.cache[key]


def spot_rate(rc: ResolveContext, currency: str) -> Decimal | None:
    """Base-currency units per 1 ``currency``: the fx_position fact's spot, else
    the run's, else the platform's preferred market spot at period end."""
    if currency == rc.bank.currency:
        return Decimal("1")
    key = f"bsd13:spot:{currency}"
    if key in rc.cache:
        return rc.cache[key]
    spot = _dec(_fx_facts(rc).get(currency, {}).get("spot_ghs"))
    if spot is None:
        spot = _dec(_run_currencies(rc).get(currency, {}).get("spot_ghs"))
    if spot is None:
        view = market_data_sources.preferred_fx_spot(
            rc.db,
            rc.ctx.organization_id,
            rc.bank.id,
            currency,
            rc.bank.currency,
            rc.period.period_end,
        )
        spot = Decimal(str(view.rate)) if view is not None else None
    rc.cache[key] = spot
    return spot


@dataclass
class _Unconverted:
    """Amounts whose currency carries no governed rate, counted per currency.

    Deliberately the same shape ``sdi_capital._Exposures`` reports as
    ``unconverted_position_count`` / ``unconverted_currencies`` (audit D-21):
    an amount that cannot be converted is EXCLUDED and COUNTED, never taken at
    face value and never quietly dropped to zero. The difference is what
    happens next — the SDI summary carries the count to a filing blocker,
    whereas a BoG cell has nowhere to carry it, so the cell itself refuses.
    """

    counts: dict[str, int] = field(default_factory=dict)

    def record(self, currency: str) -> None:
        self.counts[currency] = self.counts.get(currency, 0) + 1

    @property
    def count(self) -> int:
        return sum(self.counts.values())

    @property
    def currencies(self) -> tuple[str, ...]:
        return tuple(sorted(self.counts))

    def __bool__(self) -> bool:
        return bool(self.counts)


def _to_report_currency(
    rc: ResolveContext, amount: Decimal, currency: str, unconverted: _Unconverted
) -> Decimal:
    """``amount`` (in ``currency``) restated in the bank's reporting currency.

    Returns zero and COUNTS the currency when no governed rate resolves; the
    caller must inspect ``unconverted`` and refuse the whole line before using
    the running total, so that zero is never filed.
    """
    if amount == _ZERO:
        return _ZERO  # a nil amount is nil at every rate — no rate is required
    spot = spot_rate(rc, currency)
    if spot is None:
        unconverted.record(currency)
        return _ZERO
    return amount * spot


def _refuse_unconverted(
    rc: ResolveContext, unconverted: _Unconverted, *, metric_id: str, subject: str
) -> NotComputable:
    """The refusal a cedi-equivalent cell raises instead of assuming parity.

    Audit D-13: this line used to value an unrateable currency at 1.00 against
    the reporting currency, which reports the foreign amount itself as though
    it were already converted — on a currency trading near 14 to the unit, a
    ~14x overstatement of a FILED net open position, in a plausible-looking
    cell. A rate nobody governs is not a rate.

    The engine catches this at the resolver boundary, leaves the cell blank
    with status ``input_required``, and records the message in the form's
    completion notes — so the return states what is missing instead of
    carrying an invented figure.
    """
    listed = ", ".join(unconverted.currencies)
    return NotComputable(
        outcome(
            OutcomeState.MISSING_REQUIRED_INPUT,
            metric_id=metric_id,
            reason=(
                f"{unconverted.count} {subject} in {listed} carry no exchange rate for "
                f"{rc.period.period_end.isoformat()}: none was ingested, the "
                f"foreign-exchange run holds none, and no market rate is published. "
                f"They cannot be stated in {rc.bank.currency}, and valuing them at par "
                f"would report the foreign amount as though it were already "
                f"{rc.bank.currency}. Ingest a rate for {listed} at this reporting date."
            ),
            items=tuple(f"fx_spot:{ccy}" for ccy in unconverted.currencies),
        )
    )


def _named(params: dict[str, Any]) -> tuple[str, ...]:
    named = params.get("named")
    return tuple(str(c).upper() for c in named) if named else NAMED_CURRENCIES


def _currency_of(rc: ResolveContext, params: dict[str, Any]) -> str:
    raw = params.get("currency")
    if raw is None:
        raw = COLUMN_CURRENCIES.get(rc.column, rc.column)
    return str(raw).upper() if str(raw).lower() != OTHER else OTHER


def _is_other(currency: str, rc: ResolveContext, named: tuple[str, ...]) -> bool:
    return currency != rc.bank.currency and currency not in named


# ---------------------------------------------------------------------------
# the FX contract book (FX_HEDGE positions the engine nets)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Contract:
    source_reference: str
    sell: str
    buy: str
    notional: Decimal  # in the SELL currency
    rate: Decimal | None  # buy units per sell unit
    kind: str  # spot | forward
    origination: date | None
    maturity: date | None
    counterparty: str | None

    @property
    def buy_amount(self) -> Decimal | None:
        if self.rate is None or self.rate <= _ZERO:
            return None
        return self.notional * self.rate


def _latest_snapshots(rc: ResolveContext) -> Any:
    return (
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


def _classify(attrs: dict[str, Any], maturity: date | None, as_of: date) -> str:
    settlement = str(attrs.get("settlement") or "").strip().lower()
    if settlement in ("spot", "forward"):
        return settlement
    slug = str(attrs.get("instrument") or "").strip().lower().replace(" ", "_").replace("-", "_")
    if slug in _SPOT_INSTRUMENTS:
        return "spot"
    if maturity is not None and maturity <= as_of + timedelta(days=_SPOT_SETTLEMENT_DAYS):
        return "spot"
    return "forward"


def contracts(rc: ResolveContext) -> tuple[Contract, ...]:
    """The FX contract book: current-generation ``FX_HEDGE`` positions."""
    key = "bsd13:contracts"
    if key in rc.cache:
        return rc.cache[key]
    latest = _latest_snapshots(rc)
    stmt = (
        select(
            CanonicalPosition.source_reference,
            CanonicalPosition.currency,
            CanonicalPosition.origination_date,
            CanonicalPositionSnapshot.balance,
            CanonicalPositionSnapshot.notional,
            CanonicalPositionSnapshot.contractual_maturity,
            CanonicalPositionSnapshot.attributes,
            CanonicalCounterparty.name,
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
            CanonicalPosition.position_type == "FX_HEDGE",
        )
    )
    base = rc.bank.currency
    book: list[Contract] = []
    for ref, currency, origination, balance, notional, maturity, attrs, cp_name in rc.db.execute(
        stmt
    ):
        attributes = dict(attrs or {})
        sell = str(attributes.get("sell_currency") or currency).strip().upper()
        buy = str(attributes.get("buy_currency") or base).strip().upper()
        amount = Decimal(str(balance if balance is not None else (notional or 0)))
        book.append(
            Contract(
                source_reference=str(ref),
                sell=sell,
                buy=buy,
                notional=abs(amount),
                rate=_dec(attributes.get("contract_rate")),
                kind=_classify(attributes, maturity, rc.period.period_end),
                origination=origination,
                maturity=maturity,
                counterparty=str(cp_name) if cp_name else None,
            )
        )
    book.sort(key=lambda c: (c.maturity or date.max, c.source_reference))
    rc.cache[key] = tuple(book)
    return rc.cache[key]


def _contract_measure(
    rc: ResolveContext, currency: str, measure: str, named: tuple[str, ...]
) -> Decimal | None:
    """Σ purchases (+) or sales (−) of ``currency`` under spot / forward
    contracts; for ``other`` the cedi equivalent over every other currency.

    A named column is reported in ITS OWN currency's units, so no conversion
    happens and no rate is needed. The *Other Currencies* column is an
    aggregate in the reporting currency, so every leg it absorbs needs a
    governed rate — and the column refuses when one is missing (audit D-13).
    """
    kind, side = measure.split("_", 1)  # spot|forward, long|short
    book = [c for c in contracts(rc) if c.kind == kind]
    if not book:
        return None
    other = currency == OTHER

    def qualifies(ccy: str) -> bool:
        return _is_other(ccy, rc, named) if other else ccy == currency

    total = _ZERO
    unconverted = _Unconverted()
    for contract in book:
        if side == "long" and qualifies(contract.buy):
            amount = contract.buy_amount
            if amount is None:
                continue
            total += _to_report_currency(rc, amount, contract.buy, unconverted) if other else amount
        elif side == "short" and qualifies(contract.sell):
            total -= (
                _to_report_currency(rc, contract.notional, contract.sell, unconverted)
                if other
                else contract.notional
            )
    if unconverted:
        raise _refuse_unconverted(
            rc,
            unconverted,
            metric_id=f"bsd13.nop.{measure}.other",
            subject=f"outstanding {kind} contract leg(s)",
        )
    return total


# ---------------------------------------------------------------------------
# @resolver bsd13.nop — the FX engine's per-currency / aggregate NOP figures
# ---------------------------------------------------------------------------


@resolver("bsd13.nop")
def _nop(rc: ResolveContext, params: dict[str, Any]) -> Decimal | None:  # noqa: PLR0911
    """Params: ``currency`` ("USD" | "GBP" | "DEM" | "other" | any ISO code;
    defaults from the column key usd/gbp/dem/other), ``measure``:

    per currency (currency UNITS for a named currency; cedi equivalent for
    ``other``): ``assets`` · ``liabilities`` · ``net_assets`` (A) ·
    ``net_trading`` (C) · ``net`` (NOP in currency = the FX run's ``net_ccy``,
    fact fallback) · ``net_ghs`` (cedi equivalent = the run's ``net_ghs``) ·
    ``spot`` · ``spot_long`` / ``spot_short`` / ``forward_long`` /
    ``forward_short`` (contract book; sales negative);

    book-level (cedis / percent): ``net_worth`` (Tier 1 = NOF), ``afop``
    (the run's aggregate NOP), ``afop_pct_nof``, ``aggregate_limit_pct``,
    ``single_limit_pct``, ``sum_long_ghs``, ``sum_short_ghs``.

    ``None`` (⇒ input_required) when no succeeded FX run / fx_position fact /
    contract exists for the period.
    """
    measure = str(params.get("measure", "net"))
    named = _named(params)
    if measure in _BOOK_METRICS:
        run = _fx_run(rc)
        if run is None:
            return None
        return _dec((run.metrics or {}).get(_BOOK_METRICS[measure]))
    currency = _currency_of(rc, params)
    if measure in _CONTRACT_MEASURES:
        return _contract_measure(rc, currency, measure, named)
    if measure == "spot":
        return None if currency == OTHER else spot_rate(rc, currency)
    facts = _fx_facts(rc)
    run_ccy = _run_currencies(rc)
    if currency != OTHER:
        return _currency_measure(currency, measure, facts.get(currency), run_ccy.get(currency))
    total = _ZERO
    seen = False
    unconverted = _Unconverted()
    for ccy in sorted(set(facts) | set(run_ccy)):
        if not _is_other(ccy, rc, named):
            continue
        value = _currency_measure(ccy, measure, facts.get(ccy), run_ccy.get(ccy))
        if value is None:
            continue
        seen = True
        if measure in ("net_ghs",):
            total += value  # already in the reporting currency
        else:  # currency units → reporting-currency equivalent
            total += _to_report_currency(rc, value, ccy, unconverted)
    if unconverted:
        raise _refuse_unconverted(
            rc,
            unconverted,
            metric_id=f"bsd13.nop.{measure}.other",
            subject="open position(s)",
        )
    return total if seen else None


def _currency_measure(  # noqa: PLR0911
    currency: str,
    measure: str,
    fact: dict[str, Any] | None,
    run_ccy: dict[str, Any] | None,
) -> Decimal | None:
    if measure in _MEASURE_ROW_ATTR:
        return _dec(fact.get(_MEASURE_ROW_ATTR[measure])) if fact else None
    if measure == "net_assets":
        if not fact:
            return None
        assets = _dec(fact.get("assets_ccy"))
        liabilities = _dec(fact.get("liabilities_ccy"))
        if assets is None and liabilities is None:
            return None
        return (assets or _ZERO) - (liabilities or _ZERO)
    if measure == "net":
        if run_ccy is not None:
            return _dec(run_ccy.get("net_ccy"))
        return _dec(fact.get("net_ccy")) if fact else None
    if measure == "net_ghs":
        if run_ccy is not None:
            return _dec(run_ccy.get("net_ghs"))
        return _dec(fact.get("net_ghs", fact.get("amount"))) if fact else None
    msg = f"bsd13.nop: unknown measure {measure!r} for currency {currency!r}"
    raise ValueError(msg)


# ---------------------------------------------------------------------------
# @resolver bsd13.positions_ccy — positions.sum restricted to ONE currency
# ---------------------------------------------------------------------------


@resolver("bsd13.positions_ccy")
def _positions_ccy(rc: ResolveContext, params: dict[str, Any]) -> Decimal:  # noqa: PLR0912
    """Σ snapshot ``balance`` (or ``notional``) of the current generation of
    positions in ONE currency (``currency`` param or the column key), or — for
    ``other`` — the cedi equivalent over every non-base currency outside the
    named set (``balance_ghs`` attribute, else balance × the platform spot).

    Filters: ``position_types``, ``counterparty_types``, ``resident`` (True |
    False | "unknown_as_resident"), ``has_counterparty`` (bool),
    ``has_maturity`` (bool), ``attribute_eq``, ``attribute_in`` ({key: [..]},
    ``attribute_missing_ok`` admits rows without the key), ``attribute_not_in``,
    ``measure`` ("balance" | "notional"), ``sign``.
    """
    currency = _currency_of(rc, params)
    named = _named(params)
    measure = (
        CanonicalPositionSnapshot.notional
        if params.get("measure") == "notional"
        else CanonicalPositionSnapshot.balance
    )
    latest = _latest_snapshots(rc)
    stmt = (
        select(
            CanonicalPosition.currency,
            measure,
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
            CanonicalPositionSnapshot.withdrawn_at.is_(None),
            CanonicalPositionSnapshot.validation_status.in_(INCLUDED_VALIDATION_STATUSES),
            CanonicalPosition.superseded_by.is_(None),
            CanonicalPosition.withdrawn_at.is_(None),
        )
    )
    if types := params.get("position_types"):
        stmt = stmt.where(CanonicalPosition.position_type.in_(list(types)))
    if currency == OTHER:
        stmt = stmt.where(CanonicalPosition.currency.not_in([rc.bank.currency, *named]))
    else:
        stmt = stmt.where(CanonicalPosition.currency == currency)
    if params.get("has_counterparty") is not None:
        if params["has_counterparty"]:
            stmt = stmt.where(CanonicalPositionSnapshot.counterparty_id.is_not(None))
        else:
            stmt = stmt.where(CanonicalPositionSnapshot.counterparty_id.is_(None))
    if params.get("has_maturity") is not None:
        if params["has_maturity"]:
            stmt = stmt.where(CanonicalPositionSnapshot.contractual_maturity.is_not(None))
        else:
            stmt = stmt.where(CanonicalPositionSnapshot.contractual_maturity.is_(None))
    resident = params.get("resident")
    if params.get("counterparty_types") is not None or resident is not None:
        stmt = stmt.join(
            CanonicalCounterparty,
            CanonicalCounterparty.id == CanonicalPositionSnapshot.counterparty_id,
        )
        if cpt := params.get("counterparty_types"):
            stmt = stmt.where(CanonicalCounterparty.counterparty_type.in_(list(cpt)))
        if resident == "unknown_as_resident":
            stmt = stmt.where(CanonicalCounterparty.resident.is_not(False))
        elif resident is not None:
            stmt = stmt.where(CanonicalCounterparty.resident.is_(bool(resident)))
    for key, value in (params.get("attribute_eq") or {}).items():
        stmt = stmt.where(CanonicalPositionSnapshot.attributes[key].as_string() == str(value))
    total = _ZERO
    attr_in: dict[str, list[str]] = {
        k: [str(v).lower() for v in vs] for k, vs in (params.get("attribute_in") or {}).items()
    }
    attr_not_in: dict[str, list[str]] = {
        k: [str(v).lower() for v in vs] for k, vs in (params.get("attribute_not_in") or {}).items()
    }
    missing_ok = bool(params.get("attribute_missing_ok", False))
    unconverted = _Unconverted()
    for row_currency, amount, attrs in rc.db.execute(stmt):
        attributes = dict(attrs or {})
        if not _attribute_filters_pass(attributes, attr_in, attr_not_in, missing_ok):
            continue
        value = Decimal(str(amount or 0))
        if currency == OTHER:
            # The bank's own ingested conversion first; otherwise a governed
            # rate. Never par: a foreign balance carried at 1.00 files the
            # foreign amount as a reporting-currency figure (audit D-13/D-21).
            ghs = _dec(attributes.get("balance_ghs"))
            value = (
                ghs
                if ghs is not None and value != _ZERO
                else _to_report_currency(rc, value, str(row_currency), unconverted)
            )
        total += value
    if unconverted:
        raise _refuse_unconverted(
            rc,
            unconverted,
            metric_id="bsd13.positions_ccy.other",
            subject="position(s)",
        )
    return total * Decimal(str(params.get("sign", 1)))


def _attribute_filters_pass(
    attributes: dict[str, Any],
    attr_in: dict[str, list[str]],
    attr_not_in: dict[str, list[str]],
    missing_ok: bool,
) -> bool:
    for key, allowed in attr_in.items():
        raw = attributes.get(key)
        if raw is None or raw == "":
            if not missing_ok:
                return False
            continue
        if str(raw).strip().lower() not in allowed:
            return False
    for key, banned in attr_not_in.items():
        raw = attributes.get(key)
        if raw not in (None, "") and str(raw).strip().lower() in banned:
            return False
    return True


# ---------------------------------------------------------------------------
# @resolver bsd13.forward_contract — Schedule C annexure (per-deal listing)
# ---------------------------------------------------------------------------


def _side_listing(rc: ResolveContext, side: str) -> list[tuple[Contract, str, Decimal | None]]:
    """(contract, listed currency, amount in that currency) for the annexure's
    purchase (bank buys foreign currency) or sale (bank sells) table."""
    base = rc.bank.currency
    rows: list[tuple[Contract, str, Decimal | None]] = []
    for contract in contracts(rc):
        if contract.kind != "forward":
            continue
        if side == "purchase" and contract.buy != base:
            rows.append((contract, contract.buy, contract.buy_amount))
        elif side == "sale" and contract.sell != base:
            rows.append((contract, contract.sell, contract.notional))
    return rows


def _cedi_rate(rc: ResolveContext, contract: Contract) -> Decimal | None:
    """Cedis per unit of the listed foreign currency (the annexure's Rate)."""
    base = rc.bank.currency
    if contract.rate is None or contract.rate <= _ZERO:
        return None
    if contract.sell == base:  # buying foreign with cedis: rate = foreign per cedi
        return Decimal("1") / contract.rate
    if contract.buy == base:  # selling foreign for cedis: rate = cedis per foreign
        return contract.rate
    return contract.rate  # cross-currency: buy units per sell unit, as recorded


@resolver("bsd13.forward_contract")
def _forward_contract(  # noqa: PLR0911
    rc: ResolveContext, params: dict[str, Any]
) -> Decimal | int | str | None:
    """Params: ``side`` ("purchase" | "sale"), ``index`` (1-based slot),
    ``field`` (defaults to the column key): ``date`` (contract date),
    ``counterparty``, ``currency``, ``amount`` (currency units — the sheet's
    'Million divisor applies), ``period`` (days to maturity), ``rate`` (cedis
    per unit), ``points`` (rate − spot, cedis), ``delivery`` (maturity date).
    ``None`` when the slot is empty."""
    side = str(params.get("side", "purchase"))
    index = int(params.get("index", 1))
    field = str(params.get("field") or rc.column)
    listing = _side_listing(rc, side)
    if index < 1 or index > len(listing):
        return None
    contract, currency, amount = listing[index - 1]
    if field == "date":
        return contract.origination.isoformat() if contract.origination else None
    if field == "counterparty":
        return contract.counterparty
    if field == "currency":
        return currency
    if field == "amount":
        return amount
    if field == "period":
        if contract.maturity is None:
            return None
        start = contract.origination or rc.period.period_end
        return max((contract.maturity - start).days, 0)
    if field == "rate":
        return _cedi_rate(rc, contract)
    if field == "points":
        rate = _cedi_rate(rc, contract)
        spot = spot_rate(rc, currency)
        against_base = rc.bank.currency in (contract.sell, contract.buy)
        if rate is None or spot is None or not against_base:
            return None
        return rate - spot
    if field == "delivery":
        return contract.maturity.isoformat() if contract.maturity else None
    msg = f"bsd13.forward_contract: unknown field {field!r}"
    raise ValueError(msg)


__all__ = [
    "COLUMN_CURRENCIES",
    "NAMED_CURRENCIES",
    "OTHER",
    "Contract",
    "contracts",
    "spot_rate",
]
