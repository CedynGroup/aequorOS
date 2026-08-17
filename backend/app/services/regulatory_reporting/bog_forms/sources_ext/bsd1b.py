"""BSD1B (Daily Net Open Position) resolver — the FX engine's NOP by currency.

``FORM FXP`` reports, per currency column (US Dollar / GBP / EURO / Other), the
position components in UNITS OF CURRENCY (net assets, contingents, net trading
position, NOP), the cedi equivalent, revaluation rates, and an aggregate block
in cedis; ``AFOP`` repeats the aggregate (NOP per currency in cedi, AFOP, NOF,
AFOP as % of NOF — the last is the template's own formula).

Source: the latest SUCCEEDED baseline run of the FX module (``RegulatoryRun``
module ``fx``) — the same run DBK-DAILY reconstructs the daily NOP from — for
``net_ccy`` / ``net_ghs`` / ``side`` / ``spot_ghs`` / ``abs_pct_tier1`` per
currency, ``nop_ghs`` (AFOP), ``sum_long_ghs`` / ``sum_short_ghs``, ``tier1_ghs``
(Net Own Funds proxy, as DBK) and the aggregate limit; and the period's
``fx_position`` facts (the run's own inputs) for the on-balance decomposition
``assets_ccy`` / ``liabilities_ccy`` / ``net_derivatives_ccy``. No FX run ⇒
``None`` ⇒ ``input_required`` ("run the FX baseline"), exactly as DBK refuses.

Contingents (FXP row ii / SCHEDULE B) are NOT here: the FX run carries no
crystallised-contingent data (research gap G5, mirrored by DBK 102) — those
cells are ``input_required`` in the line map.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import select

from app.models import RegulatoryRun
from app.models.regulatory import BankFinancialFact

from ..sources import ResolveContext, resolver

MODULE_FX = "fx"
#: The named currency columns of FORM FXP / AFOP; every other run currency is "OTHER".
NAMED_CURRENCIES: tuple[str, ...] = ("USD", "GBP", "EUR")
OTHER = "OTHER"
LONG_FLAG = "( L )"
SHORT_FLAG = "( S )"
FLAT_FLAG = "-"

_ZERO = Decimal(0)
_THOUSAND = Decimal(1000)


def _fx_run(rc: ResolveContext, scenario: str) -> RegulatoryRun | None:
    key = f"run:{MODULE_FX}:{scenario}"
    cached = rc.cache.get(key)
    if cached is not None:
        return cached or None
    run = rc.db.scalar(
        select(RegulatoryRun)
        .where(
            RegulatoryRun.organization_id == rc.ctx.organization_id,
            RegulatoryRun.bank_id == rc.bank.id,
            RegulatoryRun.reporting_period_id == rc.period.id,
            RegulatoryRun.module == MODULE_FX,
            RegulatoryRun.scenario_code == scenario,
            RegulatoryRun.status == "succeeded",
        )
        .order_by(RegulatoryRun.created_at.desc())
        .limit(1)
    )
    rc.cache[key] = run or False
    return run


def _position_facts(rc: ResolveContext) -> dict[str, dict[str, Any]]:
    key = "bsd1b:fx_position_facts"
    cached = rc.cache.get(key)
    if cached is not None:
        return cached
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
        attributes = dict(fact.attributes or {})
        currency = str(attributes.get("currency") or fact.category)
        attributes.setdefault("net_ghs", str(fact.amount))
        facts[currency] = attributes
    rc.cache[key] = facts
    return facts


def _dec(value: Any) -> Decimal:
    return Decimal(str(value)) if value is not None else _ZERO


def _flag(value: Decimal) -> str:
    """The template's own long/short notation (SCHEDULE B: IF(x<0,"( S )",IF(x=0,"-","( L )")))."""
    if value < _ZERO:
        return SHORT_FLAG
    if value == _ZERO:
        return FLAT_FLAG
    return LONG_FLAG


def column_currency(column: str) -> tuple[str | None, bool]:
    """``"usd"`` → ("USD", False); ``"gbp_nature"`` → ("GBP", True); ``"other"`` →
    ("OTHER", False); anything else → (None, is_nature)."""
    nature = column.endswith("_nature") or column == "nature"
    base = column.removesuffix("_nature").upper()
    if base in NAMED_CURRENCIES or base == OTHER:
        return base, nature
    return None, nature


def _currency_rows(
    metrics: dict[str, Any], currency: str
) -> list[dict[str, Any]]:  # rows of metrics["currencies"] for a named currency / OTHER
    rows = [dict(row) for row in metrics.get("currencies", []) if isinstance(row, dict)]
    if currency == OTHER:
        return [row for row in rows if str(row.get("currency")) not in NAMED_CURRENCIES]
    return [row for row in rows if str(row.get("currency")) == currency]


def _measure(  # noqa: PLR0911, PLR0912 — one branch per official measure
    rc: ResolveContext, metrics: dict[str, Any], currency: str, measure: str
) -> Decimal | None:
    rows = _currency_rows(metrics, currency)
    facts = _position_facts(rc)
    if measure in {"assets", "liabilities", "net_derivatives", "net_assets"}:
        # units of currency → a single named currency only (OTHER mixes units)
        if currency == OTHER:
            return None
        attributes = facts.get(currency)
        if attributes is None:
            return None
        if measure == "net_assets":
            return _dec(attributes.get("assets_ccy")) - _dec(attributes.get("liabilities_ccy"))
        return _dec(attributes.get(f"{measure}_ccy"))
    if measure == "net":  # NOP in units of currency
        if currency == OTHER or not rows:
            return None
        return _dec(rows[0].get("net_ccy"))
    if measure == "spot":
        if currency == OTHER or not rows:
            return None
        return _dec(rows[0].get("spot_ghs"))
    if measure == "pct_nof":
        if currency == OTHER or not rows:
            return None
        return _dec(rows[0].get("abs_pct_tier1"))
    if measure in {"net_ghs", "net_ghs_thousands", "long", "short"}:
        if not rows and currency != OTHER:
            return None
        net = sum((_dec(row.get("net_ghs")) for row in rows), _ZERO)
        if measure == "net_ghs":
            return net
        if measure == "net_ghs_thousands":
            return net / _THOUSAND
        if measure == "long":
            return net if net > _ZERO else _ZERO
        return -net if net < _ZERO else _ZERO
    return None


def _aggregate(metrics: dict[str, Any], measure: str) -> Decimal | None:
    key = {
        "afop": "nop_ghs",
        "sum_long": "sum_long_ghs",
        "sum_short": "sum_short_ghs",
        "nof": "tier1_ghs",
        "afop_limit_pct": "nop_aggregate_limit_pct",
        "single_limit_pct": "nop_single_limit_pct",
        "afop_pct_nof": "nop_pct_tier1",
    }.get(measure)
    if key is None or metrics.get(key) is None:
        return None
    return _dec(metrics[key])


@resolver("bsd1b.nop")
def _nop(rc: ResolveContext, params: dict[str, Any]) -> Decimal | str | None:
    """Params: ``measure`` — per currency: ``net`` (NOP, units of currency),
    ``net_assets`` / ``assets`` / ``liabilities`` / ``net_derivatives`` (units of
    currency, from the run's fx_position inputs), ``net_ghs`` (cedi equivalent,
    base units), ``net_ghs_thousands`` (¢'000), ``long`` / ``short`` (cedi legs),
    ``spot`` (revaluation rate), ``pct_nof`` (|NOP| as % of NOF); aggregate:
    ``afop``, ``sum_long``, ``sum_short``, ``nof``, ``afop_pct_nof``,
    ``afop_limit_pct``, ``single_limit_pct``. ``currency`` (USD | GBP | EUR |
    OTHER) is explicit or inferred from the column key (``usd``, ``gbp_nature``
    …); a nature column returns the template's ``( L )`` / ``( S )`` / ``-``
    flag for the measure's sign. ``scenario`` defaults to ``baseline``."""
    run = _fx_run(rc, str(params.get("scenario", "baseline")))
    if run is None:
        return None
    metrics: dict[str, Any] = dict(run.metrics or {})
    measure = str(params.get("measure", "net"))
    inferred, nature = column_currency(rc.column)
    currency = str(params.get("currency") or inferred or "")
    if params.get("nature") is not None:
        nature = bool(params["nature"])
    if measure in {
        "afop", "sum_long", "sum_short", "nof", "afop_pct_nof", "afop_limit_pct",
        "single_limit_pct",
    }:  # fmt: skip
        value = _aggregate(metrics, measure)
    elif currency:
        value = _measure(rc, metrics, currency, measure)
    else:
        value = None
    if value is None:
        return None
    if nature:
        return _flag(value)
    return value * Decimal(str(params.get("scale", 1)))
