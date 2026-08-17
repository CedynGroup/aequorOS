"""BSD5A / BSD5B resolvers — capital adequacy return over the capital engine.

The official CAR FORMAT is BoG's pre-CRD adjusted-capital-base / adjusted-
asset-base computation: capital constituents (Guide "Composition of Capital"),
deductions, TOTAL ASSETS less zero-weight items less the printed percentage of
partially-weighted classes, plus contingents, 50% of NOP and 100% of the
3-year average gross income. Every percentage on the sheet is BoG's (printed
in the line label and in the NEW RISK WEIGHTS table); the resolvers here only
SELECT existing platform state and apply the sheet's own printed factor:

``bsd5.capital_facts``
    Σ ``capital_component`` facts selected by category names and/or platform
    tier (CET1 / AT1 / T2), signed by the platform's deduction flag. Positive
    rows take non-deduction facts (a deduction-flagged fact enters negatively
    only when its category is listed in ``include_deductions`` — an
    income-surplus deficit reduces disclosed reserves); deduction rows
    (``deduction=True``) take the flagged facts as positive amounts, which the
    template then subtracts.
``bsd5.balance_sheet_side``
    Σ ``balance_sheet`` facts whose ``attributes.side`` matches — the same
    on-balance-sheet total the capital engine's leverage exposure uses.
``bsd5.pct_of``
    the sheet's printed percentage ("80% of claims on other banks", "50% of
    Residential Mortgage Loans", "50% of NOP") applied to another resolver's
    value — the wrapped resolver and its params are named in the line map, so
    the doc shows exactly what is scaled.
``bsd5.run_line``
    one persisted line item (``exposure_amount`` / ``weighted_amount`` /
    ``rate_pct``) of the latest SUCCEEDED capital baseline run for the period
    (e.g. the larger net open FX position the market-risk charge was struck
    on). None (input_required) until the capital engine has run.
``bsd5.avg_gross_income``
    plain arithmetic mean of the run's ``gross_income_<year>`` operational-risk
    lines over the latest N (default 3) years — the sheet's "3yrs Average
    Annual Gross Income".
``bsd5.off_balance_residual``
    Σ ``off_balance`` facts (the platform's LC/guarantee book) less the
    positions the bank tagged with an ``obs_category`` reported on its own row
    (letters of credit), so the contingent-liability rows never double count.

Nothing here computes a BoG figure by a new rule; the roll-ups, the ratio and
the surplus/deficit test are the template's own formulas.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import select

from app.models import RegulatoryLineItem, RegulatoryRun
from app.models.regulatory import BankFinancialFact

from ..sources import ResolveContext, get_resolver, resolver

_HUNDRED = Decimal("100")
_ZERO = Decimal("0")


def _capital_run(rc: ResolveContext, params: dict[str, Any]) -> RegulatoryRun | None:
    """Latest succeeded run of ``module``/``scenario`` for the period (memoised
    under the same cache key ``run.metric`` uses, so one lookup serves both)."""
    module = str(params.get("module", "capital"))
    scenario = str(params.get("scenario", "baseline"))
    key = f"run:{module}:{scenario}"
    run = rc.cache.get(key)
    if run is None:
        run = rc.db.scalar(
            select(RegulatoryRun)
            .where(
                RegulatoryRun.organization_id == rc.ctx.organization_id,
                RegulatoryRun.bank_id == rc.bank.id,
                RegulatoryRun.reporting_period_id == rc.period.id,
                RegulatoryRun.module == module,
                RegulatoryRun.scenario_code == scenario,
                RegulatoryRun.status == "succeeded",
            )
            .order_by(RegulatoryRun.created_at.desc())
            .limit(1)
        )
        rc.cache[key] = run or False
    return run or None


def _run_lines(rc: ResolveContext, run: RegulatoryRun, section: str) -> list[RegulatoryLineItem]:
    key = f"run_lines:{run.id}:{section}"
    lines = rc.cache.get(key)
    if lines is None:
        lines = list(
            rc.db.scalars(
                select(RegulatoryLineItem)
                .where(
                    RegulatoryLineItem.organization_id == rc.ctx.organization_id,
                    RegulatoryLineItem.bank_id == rc.bank.id,
                    RegulatoryLineItem.run_id == run.id,
                    RegulatoryLineItem.section == section,
                )
                .order_by(RegulatoryLineItem.position)
            )
        )
        rc.cache[key] = lines
    return lines


# ---------------------------------------------------------------------------
# capital constituents (Guide: Composition of Capital)
# ---------------------------------------------------------------------------


@resolver("bsd5.capital_facts")
def _capital_facts(rc: ResolveContext, params: dict[str, Any]) -> Decimal:
    """Σ ``capital_component`` facts for the period.

    Selection: ``categories`` (names) and/or ``tiers`` (CET1/AT1/T2) minus
    ``exclude`` names. ``deduction=False`` (default) sums non-deduction facts
    positively and, for categories in ``include_deductions`` only,
    deduction-flagged facts negatively; ``deduction=True`` sums the
    deduction-flagged facts of the selection as positive amounts (the sheet
    subtracts them). ``currency`` is ignored — capital is reported in the base
    currency.
    """
    categories = [str(c) for c in params.get("categories") or ()]
    tiers = [str(t) for t in params.get("tiers") or ()]
    exclude = {str(c) for c in params.get("exclude") or ()}
    include_deductions = {str(c) for c in params.get("include_deductions") or ()}
    deduction = bool(params.get("deduction", False))
    stmt = select(
        BankFinancialFact.category, BankFinancialFact.is_deduction, BankFinancialFact.amount
    ).where(
        BankFinancialFact.organization_id == rc.ctx.organization_id,
        BankFinancialFact.bank_id == rc.bank.id,
        BankFinancialFact.reporting_period_id == rc.period.id,
        BankFinancialFact.fact_group == "capital_component",
    )
    if categories and tiers:
        stmt = stmt.where(
            BankFinancialFact.category.in_(categories) | BankFinancialFact.capital_tier.in_(tiers)
        )
    elif categories:
        stmt = stmt.where(BankFinancialFact.category.in_(categories))
    elif tiers:
        stmt = stmt.where(BankFinancialFact.capital_tier.in_(tiers))
    total = _ZERO
    for category, is_deduction, amount in rc.db.execute(stmt):
        if category in exclude:
            continue
        value = Decimal(str(amount))
        if deduction:
            if is_deduction:
                total += value
        elif not is_deduction:
            total += value
        elif category in include_deductions:
            total -= value
    return total


# ---------------------------------------------------------------------------
# balance-sheet total by side
# ---------------------------------------------------------------------------


@resolver("bsd5.balance_sheet_side")
def _balance_sheet_side(rc: ResolveContext, params: dict[str, Any]) -> Decimal:
    """Σ ``balance_sheet`` facts with ``attributes.side == side`` (default
    ``asset``) — the platform's on-balance-sheet total for the period."""
    side = str(params.get("side", "asset"))
    stmt = select(BankFinancialFact.amount).where(
        BankFinancialFact.organization_id == rc.ctx.organization_id,
        BankFinancialFact.bank_id == rc.bank.id,
        BankFinancialFact.reporting_period_id == rc.period.id,
        BankFinancialFact.fact_group == "balance_sheet",
        BankFinancialFact.attributes["side"].as_string() == side,
    )
    return sum((Decimal(str(amount)) for amount in rc.db.scalars(stmt)), _ZERO)


# ---------------------------------------------------------------------------
# the sheet's printed percentage of another resolver's value
# ---------------------------------------------------------------------------


@resolver("bsd5.pct_of")
def _pct_of(rc: ResolveContext, params: dict[str, Any]) -> Decimal | None:
    """``{"pct": 80, "source": "positions.sum", "params": {...}}`` →
    80% × positions.sum(...). None propagates (input_required)."""
    inner = get_resolver(str(params["source"]))(rc, dict(params.get("params") or {}))
    if inner is None or isinstance(inner, str):
        return None
    return Decimal(str(inner)) * Decimal(str(params["pct"])) / _HUNDRED


# ---------------------------------------------------------------------------
# capital baseline run: persisted line items
# ---------------------------------------------------------------------------


@resolver("bsd5.run_line")
def _run_line(rc: ResolveContext, params: dict[str, Any]) -> Decimal | None:
    """``{"section": "market_rwa", "line_code": "fx_charge", "field":
    "exposure_amount"}`` → that field of the latest succeeded capital baseline
    run's line item; None when there is no run / no such line."""
    run = _capital_run(rc, params)
    if run is None:
        return None
    field = str(params.get("field", "exposure_amount"))
    for line in _run_lines(rc, run, str(params["section"])):
        if line.line_code == str(params["line_code"]):
            raw = getattr(line, field)
            return None if raw is None else Decimal(str(raw))
    return None


@resolver("bsd5.avg_gross_income")
def _avg_gross_income(rc: ResolveContext, params: dict[str, Any]) -> Decimal | None:
    """Arithmetic mean of the run's ``gross_income_<year>`` operational-risk
    lines over the latest ``years`` (default 3) years — Guide: "3yrs Average
    Annual Gross Income". None until the capital engine has run."""
    run = _capital_run(rc, params)
    if run is None:
        return None
    years = int(params.get("years", 3))
    prefix = str(params.get("prefix", "gross_income"))
    dated: list[tuple[int, Decimal]] = []
    for line in _run_lines(rc, run, "operational_rwa"):
        if not line.line_code.startswith(prefix) or line.exposure_amount is None:
            continue
        suffix = line.line_code.rsplit("_", 1)[-1]
        year = int(suffix) if suffix.isdigit() else line.position
        dated.append((year, Decimal(str(line.exposure_amount))))
    if not dated:
        return None
    latest = sorted(dated)[-years:]
    return sum((amount for _, amount in latest), _ZERO) / Decimal(len(latest))


# ---------------------------------------------------------------------------
# contingent liabilities: the LC/guarantee book less rows reported separately
# ---------------------------------------------------------------------------


@resolver("bsd5.off_balance_residual")
def _off_balance_residual(rc: ResolveContext, params: dict[str, Any]) -> Decimal:
    """Σ ``off_balance`` facts (all categories, base currency — the platform's
    LC/guarantee book, which its liquidity return classifies as guarantees /
    indemnities by default) less ``positions.sum`` over the ``less`` list of
    position filters (the LC_GUARANTEE positions tagged for another row)."""
    facts_total = get_resolver("facts.sum")(
        rc,
        {
            "group": "off_balance",
            "categories": list(params.get("categories") or ()),
            "currency": "all",
        },
    )
    total = Decimal(str(facts_total or 0))
    for filters in params.get("less") or ():
        part = get_resolver("positions.sum")(rc, dict(filters))
        total -= Decimal(str(part or 0))
    return total
