"""BSD7A / BSD7B resolvers — Current Year Results (P&L).

Two sources feed the P&L forms, both existing platform state:

* ``bsd7.pl_line`` — the bank's own INCOME/EXPENSE ledger
  (``canonical_gl_accounts``). A P&L general-ledger account carries a
  fiscal-year-to-date balance as at each ``as_of_date`` (trial-balance
  convention: P&L accounts are cleared to reserves at the year end), so the
  period-to-date column is the latest generation on/before period end and the
  month / quarter columns are differences of consecutive period-to-date
  balances. An account the register marks ``balance_basis="period"`` is closed
  monthly instead: the latest generation in each calendar month is that
  month's movement and a window sums its months (an intra-month generation is
  a month-to-date figure, superseded by the later one in the same month —
  never added to it). Which accounts feed which official line is the bank's
  chart-of-accounts mapping, stated one of two ways: (1) on the ledger itself —
  an account whose ``attributes["bsd7_line"]`` equals the line tag (``"1a"``,
  ``"2a_savings"`` … the tags are the official item numbers); (2) as data —
  the reference dataset ``gl_mapping_bsd7`` (docs/data_engine/datasets/
  gl_mapping_bsd7.md), one row per ``gl_account_code`` (exact) or ``gl_prefix``
  (starts-with) naming the ``bsd7_item`` plus a per-account ``sign`` and
  ``balance_basis``. Precedence per account: its own tag, else the exact-code
  row, else the LONGEST matching prefix row, else the line map's declared
  ``account_code_prefixes``. There is no platform-wide chart of accounts, so a
  line with no selected account resolves to ``None`` (input_required) rather
  than a guessed figure.

* ``bsd7.average_facts`` — the "Average Quarter Ended / Average Period to
  date" block: the arithmetic mean over the reporting periods in the window of
  Σ ``bank_facts`` matching the filters (month-end observations — the platform
  holds monthly reporting periods; the doc states this basis).

Column keys carry the window and the currency rule: ``month_domestic``,
``month_foreign``, ``ptd_domestic``, ``ptd_foreign`` (BSD7A), ``quarter`` /
``ptd`` (BSD7B and the averages block; no currency split → all currencies).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import func, or_, select

from app.domain.gl import pl_mapping
from app.domain.gl.pl_mapping import (
    LINE_ATTRIBUTE,
    MAPPING_KIND,
    BalanceBasis,
    CoaMapping,
    CurrencyRule,
    Generation,
    MappingRule,
    Window,
    fiscal_year_start,
)
from app.domain.ingestion.constants import INCLUDED_VALIDATION_STATUSES
from app.models import BankReportingPeriod
from app.models.canonical import CanonicalGlAccount
from app.models.regulatory import BankFinancialFact

from ..sources import ResolveContext, reference_rows, require_usable_capital_register, resolver

# The pure rules — windows, the CoA → item mapping, the YTD / period-movement
# arithmetic — live in ``app/domain/gl/pl_mapping.py`` (D-021) so the BI plane's
# monthly GL mart is computed by the SAME functions this return files. This
# module keeps the database selection and the resolver registration, and
# re-exports the names its tests and line maps import.
__all__ = [
    "LINE_ATTRIBUTE",
    "MAPPING_KIND",
    "BalanceBasis",
    "CoaMapping",
    "CurrencyRule",
    "MappingRule",
    "Window",
    "coa_mapping",
    "fiscal_year_start",
    "window_start",
]

_window_of = pl_mapping.window_of
_currency_of = pl_mapping.currency_of


def window_start(period: BankReportingPeriod, window: Window, start_month: int) -> date:
    """First day of the reporting window ending at ``period.period_end``."""
    return pl_mapping.window_start(period.period_start, period.period_end, window, start_month)


# ---------------------------------------------------------------------------
# bsd7.pl_line — P&L ledger lines
# ---------------------------------------------------------------------------

_mapping_rule = pl_mapping.mapping_rule


def coa_mapping(rc: ResolveContext) -> CoaMapping:
    """The latest ``gl_mapping_bsd7`` register on/before period end (memoised per
    form computation); empty when the bank has not ingested one."""
    key = f"bsd7:{MAPPING_KIND}"
    cached = rc.cache.get(key)
    if cached is not None:
        return cached
    mapping = pl_mapping.coa_mapping_from_rows(reference_rows(rc, MAPPING_KIND))
    rc.cache[key] = mapping
    return mapping


_Generation = Generation


def _selected_generations(
    rc: ResolveContext, params: dict[str, Any], lower: date, upper: date
) -> list[_Generation]:
    """Every current-generation row of the accounts the line selects with as_of ∈
    [lower, upper] (all currencies), with the account's effective sign and basis.

    Selection precedence per account: its own ``attributes.bsd7_line`` tag; else
    the ``gl_mapping_bsd7`` exact-code row; else the longest matching prefix
    row; else the line map's ``account_code_prefixes`` (declared selection —
    kept regardless of the register, as before the register existed).
    """
    line = params.get("line")
    line_tag = str(line) if line else None
    mapping = coa_mapping(rc) if line_tag else CoaMapping({}, ())
    mapped_codes, mapped_prefixes = mapping.selectors_for(line_tag) if line_tag else ([], [])
    declared_prefixes = [str(p) for p in params.get("account_code_prefixes") or ()]

    stmt = select(
        CanonicalGlAccount.account_code,
        CanonicalGlAccount.as_of_date,
        CanonicalGlAccount.currency,
        CanonicalGlAccount.balance,
        CanonicalGlAccount.attributes[LINE_ATTRIBUTE].as_string(),
    ).where(
        CanonicalGlAccount.organization_id == rc.ctx.organization_id,
        CanonicalGlAccount.bank_id == rc.bank.id,
        CanonicalGlAccount.superseded_by.is_(None),
        CanonicalGlAccount.withdrawn_at.is_(None),
        CanonicalGlAccount.validation_status.in_(INCLUDED_VALIDATION_STATUSES),
        CanonicalGlAccount.balance.is_not(None),
        CanonicalGlAccount.as_of_date >= lower,
        CanonicalGlAccount.as_of_date <= upper,
    )
    if classes := params.get("gl_classes"):
        stmt = stmt.where(CanonicalGlAccount.account_class.in_(list(classes)))
    selectors = []
    if line_tag:
        selectors.append(CanonicalGlAccount.attributes[LINE_ATTRIBUTE].as_string() == line_tag)
    if mapped_codes:
        selectors.append(CanonicalGlAccount.account_code.in_(mapped_codes))
    for prefix in (*mapped_prefixes, *declared_prefixes):
        selectors.append(CanonicalGlAccount.account_code.startswith(prefix))
    if not selectors:
        msg = "bsd7.pl_line needs a 'line' tag and/or 'account_code_prefixes'"
        raise ValueError(msg)
    stmt = stmt.where(or_(*selectors))

    default_basis: BalanceBasis = str(params.get("balance_basis", "ytd"))
    out: list[_Generation] = []
    for code, as_of, currency, balance, tag in rc.db.execute(stmt).all():
        account_code = str(code)
        rule = _effective_rule(account_code, tag, line_tag, mapping, declared_prefixes)
        if rule is None:
            continue
        signed = Decimal(balance) * rule.sign
        out.append(_Generation(account_code, as_of, currency, signed, rule.basis or default_basis))
    return out


_effective_rule = pl_mapping.effective_rule


def _in_currency(rc: ResolveContext, currency: str | None) -> bool:
    """Guide §2 per column: Domestic = the bank's base currency (a ledger account with
    no stated currency is a base-currency account); Foreign = any other."""
    return pl_mapping.in_currency(_currency_of(rc.column), currency, rc.bank.currency)


def _ytd_total(rc: ResolveContext, rows: list[_Generation], upper: date) -> Decimal:
    """Σ balance of the latest generation per account code with as_of ≤ ``upper``,
    in this column's currency slice."""
    return pl_mapping.ytd_total(rows, upper, in_slice=lambda currency: _in_currency(rc, currency))


def _period_total(rc: ResolveContext, rows: list[_Generation], lower: date) -> Decimal:
    """Σ balance of the latest generation per account code per calendar month
    with as_of ≥ ``lower``, in this column's currency slice — period-movement
    ledgers (the H-006 rule; ``pl_mapping.period_total`` states it)."""
    return pl_mapping.period_total(
        rows, lower, in_slice=lambda currency: _in_currency(rc, currency)
    )


@resolver("bsd7.pl_line")
def _pl_line(rc: ResolveContext, params: dict[str, Any]) -> Decimal | None:
    """One official P&L line from the bank's INCOME/EXPENSE ledger.

    params: ``line`` (official item tag — matched on GL ``attributes.bsd7_line``
    or on the bank's ``gl_mapping_bsd7`` register), ``account_code_prefixes``
    (alternative/complementary selection), ``gl_classes`` (default any),
    ``balance_basis`` ``"ytd"`` (default; balances are fiscal-year-to-date,
    month/quarter = difference of consecutive period-to-date balances) |
    ``"period"`` (the account is closed monthly: each generation is its
    month-to-date movement, the latest generation in a month is that month's
    movement, and the window sums the months) — a register row's
    ``balance_basis`` overrides it per account,
    ``fiscal_year_start_month`` (default 1), ``sign`` (default 1; a register
    row's ``sign`` applies per account on top). Returns None when no account is
    selected for the line in the fiscal year (the bank's CoA mapping has not
    named it), or when a month/quarter split would need a prior-period
    generation the ledger does not hold; a currency slice with no selected
    account reads 0 (the line IS mapped, nothing arose in that currency).
    """
    start_month = int(params.get("fiscal_year_start_month", 1))
    window = _window_of(rc.column)
    lower = window_start(rc.period, window, start_month)
    upper = rc.period.period_end
    sign = Decimal(str(params.get("sign", 1)))
    fy_start = fiscal_year_start(upper, start_month)
    rows = _selected_generations(rc, params, fy_start, upper)
    return pl_mapping.line_total(
        rows,
        window=window,
        lower=lower,
        upper=upper,
        fy_start=fy_start,
        in_slice=lambda currency: _in_currency(rc, currency),
        sign=sign,
    )


# ---------------------------------------------------------------------------
# bsd7.average_facts — averages block (rows 38–42 of BSD7A)
# ---------------------------------------------------------------------------


@resolver("bsd7.average_facts")
def _average_facts(rc: ResolveContext, params: dict[str, Any]) -> Decimal | None:
    """Mean over the reporting periods in the window (``quarter`` | ``ptd``
    from the column key) of Σ ``bank_facts.amount`` matching ``group`` and
    optional ``categories`` / ``attribute_eq`` / ``capital_tiers`` /
    ``exclude_deductions``; all currencies. Periods without a matching fact are
    not observations; None when there are none.
    """
    start_month = int(params.get("fiscal_year_start_month", 1))
    lower = window_start(rc.period, _window_of(rc.column), start_month)
    if params["group"] == "capital_component":
        require_usable_capital_register(rc, window_start=lower)
    stmt = (
        select(
            BankFinancialFact.reporting_period_id,
            func.coalesce(func.sum(BankFinancialFact.amount), 0),
        )
        .join(BankReportingPeriod, BankReportingPeriod.id == BankFinancialFact.reporting_period_id)
        .where(
            BankFinancialFact.organization_id == rc.ctx.organization_id,
            BankFinancialFact.bank_id == rc.bank.id,
            BankFinancialFact.fact_group == params["group"],
            BankReportingPeriod.period_end >= lower,
            BankReportingPeriod.period_end <= rc.period.period_end,
        )
        .group_by(BankFinancialFact.reporting_period_id)
    )
    if categories := params.get("categories"):
        stmt = stmt.where(BankFinancialFact.category.in_(list(categories)))
    if tiers := params.get("capital_tiers"):
        stmt = stmt.where(BankFinancialFact.capital_tier.in_(list(tiers)))
    if params.get("exclude_deductions"):
        stmt = stmt.where(BankFinancialFact.is_deduction.is_(False))
    for key, value in (params.get("attribute_eq") or {}).items():
        stmt = stmt.where(BankFinancialFact.attributes[key].as_string() == str(value))
    rows = rc.db.execute(stmt).all()
    if not rows:
        return None
    total = sum((Decimal(amount or 0) for _, amount in rows), Decimal(0))
    return (total / Decimal(len(rows))) * Decimal(str(params.get("sign", 1)))
