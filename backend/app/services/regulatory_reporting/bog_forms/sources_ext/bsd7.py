"""BSD7A / BSD7B resolvers — Current Year Results (P&L).

Two sources feed the P&L forms, both existing platform state:

* ``bsd7.pl_line`` — the bank's own INCOME/EXPENSE ledger
  (``canonical_gl_accounts``). A P&L general-ledger account carries a
  fiscal-year-to-date balance as at each ``as_of_date`` (trial-balance
  convention: P&L accounts are cleared to reserves at the year end), so the
  period-to-date column is the latest generation on/before period end and the
  month / quarter columns are differences of consecutive period-to-date
  balances. Which accounts feed which official line is the bank's
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

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, or_, select

from app.models import BankReportingPeriod
from app.models.canonical import CanonicalGlAccount
from app.models.regulatory import BankFinancialFact

from ..sources import ResolveContext, reference_rows, resolver

#: Ledger rows the platform's own derivation admits (fact_derivation._INCLUDED_VALIDATION_STATUSES).
_ACCEPTED = ("accepted", "warning")
#: Attribute key on a P&L GL account naming the official BSD7A/BSD7B item it feeds.
LINE_ATTRIBUTE = "bsd7_line"
#: Reference dataset carrying the bank's CoA → BSD7 item mapping as data.
MAPPING_KIND = "gl_mapping_bsd7"

type Window = str  # month | quarter | ptd
type CurrencyRule = str  # domestic | foreign | all
type BalanceBasis = str  # ytd | period


def _window_of(column: str) -> Window:
    if column.startswith("month"):
        return "month"
    if column.startswith("quarter"):
        return "quarter"
    return "ptd"


def _currency_of(column: str) -> CurrencyRule:
    if column.endswith("_domestic"):
        return "domestic"
    if column.endswith("_foreign"):
        return "foreign"
    return "all"


def fiscal_year_start(period_end: date, start_month: int) -> date:
    year = period_end.year if period_end.month >= start_month else period_end.year - 1
    return date(year, start_month, 1)


def window_start(period: BankReportingPeriod, window: Window, start_month: int) -> date:
    """First day of the reporting window ending at ``period.period_end``."""
    fy_start = fiscal_year_start(period.period_end, start_month)
    if window == "ptd":
        return fy_start
    if window == "month":
        return max(period.period_start, fy_start)
    # quarter: the fiscal quarter containing the period end
    months_into_year = (period.period_end.year - fy_start.year) * 12 + (
        period.period_end.month - fy_start.month
    )
    quarter_offset = months_into_year - (months_into_year % 3)
    year = fy_start.year + (fy_start.month - 1 + quarter_offset) // 12
    month = (fy_start.month - 1 + quarter_offset) % 12 + 1
    return date(year, month, 1)


# ---------------------------------------------------------------------------
# bsd7.pl_line — P&L ledger lines
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MappingRule:
    """One ``gl_mapping_bsd7`` register row, normalised."""

    item: str
    sign: Decimal
    basis: BalanceBasis | None  # None = the line map's / resolver's default


@dataclass(frozen=True)
class CoaMapping:
    """The bank's CoA → BSD7 item register: exact codes and prefixes (longest first)."""

    codes: dict[str, MappingRule]
    prefixes: tuple[tuple[str, MappingRule], ...]

    def rule_for(self, account_code: str) -> MappingRule | None:
        exact = self.codes.get(account_code)
        if exact is not None:
            return exact
        for prefix, rule in self.prefixes:
            if account_code.startswith(prefix):
                return rule
        return None

    def selectors_for(self, item: str) -> tuple[list[str], list[str]]:
        """(exact codes, prefixes) the register maps to ``item`` — a pre-filter only;
        :meth:`rule_for` decides the effective item per account."""
        codes = [code for code, rule in self.codes.items() if rule.item == item]
        prefixes = [prefix for prefix, rule in self.prefixes if rule.item == item]
        return codes, prefixes


def _mapping_rule(row: dict[str, Any]) -> MappingRule | None:
    item = str(row.get("bsd7_item") or "").strip()
    if not item:
        return None
    try:
        sign = Decimal(str(row.get("sign") or "1").strip())
    except ArithmeticError:
        sign = Decimal(1)
    basis_text = str(row.get("balance_basis") or "").strip().lower()
    basis: BalanceBasis | None = basis_text if basis_text in ("ytd", "period") else None
    return MappingRule(item=item, sign=sign, basis=basis)


def coa_mapping(rc: ResolveContext) -> CoaMapping:
    """The latest ``gl_mapping_bsd7`` register on/before period end (memoised per
    form computation); empty when the bank has not ingested one."""
    key = f"bsd7:{MAPPING_KIND}"
    cached = rc.cache.get(key)
    if cached is not None:
        return cached
    codes: dict[str, MappingRule] = {}
    prefixes: dict[str, MappingRule] = {}
    for row in reference_rows(rc, MAPPING_KIND):
        rule = _mapping_rule(row)
        if rule is None:
            continue
        code = str(row.get("gl_account_code") or "").strip()
        prefix = str(row.get("gl_prefix") or "").strip()
        if code:
            codes[code] = rule
        elif prefix:
            prefixes[prefix] = rule
    mapping = CoaMapping(
        codes=codes,
        prefixes=tuple(sorted(prefixes.items(), key=lambda kv: len(kv[0]), reverse=True)),
    )
    rc.cache[key] = mapping
    return mapping


@dataclass(frozen=True)
class _Generation:
    code: str
    as_of: date
    currency: str | None
    balance: Decimal  # already carries the account's mapping sign
    basis: BalanceBasis


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
        CanonicalGlAccount.validation_status.in_(_ACCEPTED),
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


def _effective_rule(
    account_code: str,
    tag: Any,
    line_tag: str | None,
    mapping: CoaMapping,
    declared_prefixes: list[str],
) -> MappingRule | None:
    """The rule under which ``account_code`` feeds ``line_tag`` — or None when it
    does not: own tag > register exact code > register longest prefix > the line
    map's declared prefixes (default sign/basis)."""
    if tag not in (None, ""):
        selected = line_tag is not None and str(tag) == line_tag
        rule = MappingRule(item=str(tag), sign=Decimal(1), basis=None) if selected else None
    else:
        rule = mapping.rule_for(account_code) if line_tag else None
        if rule is not None and rule.item != line_tag:
            rule = None
    if rule is None and any(account_code.startswith(p) for p in declared_prefixes):
        rule = MappingRule(item=line_tag or "", sign=Decimal(1), basis=None)
    return rule


def _in_currency(rc: ResolveContext, currency: str | None) -> bool:
    """Guide §2 per column: Domestic = the bank's base currency (a ledger account with
    no stated currency is a base-currency account); Foreign = any other."""
    rule = _currency_of(rc.column)
    if rule == "all":
        return True
    is_base = currency is None or currency == rc.bank.currency
    return is_base if rule == "domestic" else not is_base


def _ytd_total(rc: ResolveContext, rows: list[_Generation], upper: date) -> Decimal:
    """Σ balance of the latest generation per account code with as_of ≤ ``upper``,
    in this column's currency slice."""
    latest: dict[str, _Generation] = {}
    for row in rows:
        if row.as_of > upper:
            continue
        current = latest.get(row.code)
        if current is None or row.as_of > current.as_of:
            latest[row.code] = row
    return sum(
        (row.balance for row in latest.values() if _in_currency(rc, row.currency)), Decimal(0)
    )


def _period_total(rc: ResolveContext, rows: list[_Generation], lower: date) -> Decimal:
    """Σ balance over every generation with as_of ≥ ``lower`` in this column's
    currency slice — period-movement ledgers."""
    return sum(
        (row.balance for row in rows if row.as_of >= lower and _in_currency(rc, row.currency)),
        Decimal(0),
    )


@resolver("bsd7.pl_line")
def _pl_line(rc: ResolveContext, params: dict[str, Any]) -> Decimal | None:
    """One official P&L line from the bank's INCOME/EXPENSE ledger.

    params: ``line`` (official item tag — matched on GL ``attributes.bsd7_line``
    or on the bank's ``gl_mapping_bsd7`` register), ``account_code_prefixes``
    (alternative/complementary selection), ``gl_classes`` (default any),
    ``balance_basis`` ``"ytd"`` (default; balances are fiscal-year-to-date,
    month/quarter = difference of consecutive period-to-date balances) |
    ``"period"`` (each generation is that as-of's movement; the window sums
    them) — a register row's ``balance_basis`` overrides it per account,
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
    # period-movement accounts: the window's generations ARE the movement
    period_rows = [row for row in rows if row.basis == "period" and row.as_of >= lower]
    ytd_rows = [row for row in rows if row.basis != "period"]
    if not period_rows and not ytd_rows:
        return None
    total = _period_total(rc, period_rows, lower)
    if ytd_rows:
        current = _ytd_total(rc, ytd_rows, upper)
        if window == "ptd":
            total += current
        else:
            prior_end = lower - timedelta(days=1)
            if prior_end < fy_start:
                total += current  # first window of the fiscal year: nothing to net off
            elif not any(row.as_of <= prior_end for row in ytd_rows):
                return None  # cannot split the year-to-date figure honestly
            else:
                total += current - _ytd_total(rc, ytd_rows, prior_end)
    return total * sign


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
