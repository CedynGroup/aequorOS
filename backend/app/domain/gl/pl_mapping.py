"""BSD7's P&L ledger rules, lifted pure (D-021; recon §B4).

Everything here was extracted from
``app/services/regulatory_reporting/bog_forms/sources_ext/bsd7.py`` without
changing a figure: BSD7 now delegates to these functions (its own tests pin
byte-identical output), and ``bi_fact_gl_monthly`` is computed from the same
functions, so the mart can only ever say what the filed return says. Nothing
here opens a session; every input is a plain value or a sequence of
:class:`Generation` rows the caller selected.

The contract this module states (``.ai/bi_recon/h_b5_report.md`` §2):

* A P&L general-ledger account carries a **fiscal-year-to-date** balance as at
  each ``as_of_date`` (``ytd`` basis, the default): the period-to-date figure is
  the latest generation on/before the window's end, and a month / quarter is the
  difference of consecutive period-to-date balances — ``None`` when the ledger
  holds no generation on/before the prior window's end, never the YTD figure
  passed off as a month.
* An account the register marks ``period`` basis is **closed monthly**: the
  latest generation in each calendar month is that month's movement and a
  window sums its months; an intra-month generation is a month-to-date figure
  superseded by the later one in the same month (H-006).
* Windows always begin on day 1 of a month, so a calendar month never
  straddles one; the fiscal year starts on day 1 of ``start_month``.
* Which official line an account feeds is the bank's own statement, in
  precedence: the account's own ``attributes["bsd7_line"]`` tag, else the
  ``gl_mapping_bsd7`` register's exact-code row, else its LONGEST matching
  prefix row, else the line map's declared ``account_code_prefixes``.
* Domestic = the bank's base currency (a ledger account with no stated
  currency is a base-currency account); Foreign = any other; Total = all.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

type Window = str  # month | quarter | ptd
type CurrencyRule = str  # domestic | foreign | all
type BalanceBasis = str  # ytd | period

#: Attribute key on a P&L GL account naming the official BSD7A/BSD7B item it feeds.
LINE_ATTRIBUTE = "bsd7_line"
#: Reference dataset carrying the bank's CoA → BSD7 item mapping as data.
MAPPING_KIND = "gl_mapping_bsd7"

YTD: BalanceBasis = "ytd"
PERIOD: BalanceBasis = "period"
BALANCE_BASES: tuple[BalanceBasis, ...] = (YTD, PERIOD)

#: The signs the ``gl_mapping_bsd7`` register admits: ``1`` normal, ``-1`` contra.
#: This is the INGESTION CONTRACT's own enum
#: (``domain/ingestion/reference_schemas/gl_mapping_bsd7.SIGNS``), pinned against
#: it by ``tests/domain/gl/test_pl_mapping.py`` so a third value cannot appear
#: there without this vocabulary noticing. :func:`mapping_rule` deliberately
#: does NOT enforce it — BSD7 has always taken the register's number as given
#: and multiplies it into the filed line, so narrowing the parse would change a
#: filed figure. A consumer that can only REPRESENT an integral sign enforces it
#: at its own boundary instead (:func:`register_sign_as_int`, A5-08).
REGISTER_SIGNS: tuple[Decimal, ...] = (Decimal(1), Decimal(-1))

#: BSD7's fiscal year starts in January unless a line map says otherwise; no
#: line map does, and no bank-level setting exists (recon §B4). The BI plane
#: uses the same default so its fiscal columns agree with the return.
DEFAULT_FISCAL_YEAR_START_MONTH = 1

#: The ledger classes whose balances are fiscal-year-to-date figures under the
#: trial-balance convention above. Balance-sheet classes are stocks, not flows.
PL_ACCOUNT_CLASSES: tuple[str, ...] = ("INCOME", "EXPENSE")

_ONE = Decimal(1)
_ZERO = Decimal(0)


# ---------------------------------------------------------------------------
# column keys and windows
# ---------------------------------------------------------------------------


def window_of(column: str) -> Window:
    """``month`` / ``quarter`` / ``ptd`` from a BSD7 column key."""
    if column.startswith("month"):
        return "month"
    if column.startswith("quarter"):
        return "quarter"
    return "ptd"


def currency_of(column: str) -> CurrencyRule:
    """``domestic`` / ``foreign`` / ``all`` from a BSD7 column key."""
    if column.endswith("_domestic"):
        return "domestic"
    if column.endswith("_foreign"):
        return "foreign"
    return "all"


def fiscal_year_start(period_end: date, start_month: int) -> date:
    """Day 1 of the fiscal year ``period_end`` falls in."""
    year = period_end.year if period_end.month >= start_month else period_end.year - 1
    return date(year, start_month, 1)


def fiscal_year(day: date, start_month: int) -> int:
    """The fiscal year label: the calendar year the fiscal year STARTS in.

    With a January start this is the calendar year; with a July start, every
    day from 2025-07-01 to 2026-06-30 is fiscal 2025.
    """
    return fiscal_year_start(day, start_month).year


def fiscal_quarter(day: date, start_month: int) -> int:
    """1–4: the fiscal quarter ``day`` falls in."""
    fy_start = fiscal_year_start(day, start_month)
    months_into_year = (day.year - fy_start.year) * 12 + (day.month - fy_start.month)
    return months_into_year // 3 + 1


def window_start(period_start: date, period_end: date, window: Window, start_month: int) -> date:
    """First day of the reporting window ending at ``period_end``.

    ``ptd`` = the fiscal year start; ``month`` = the reporting period's own
    start, clipped to the fiscal year; ``quarter`` = day 1 of the fiscal quarter
    containing ``period_end``.
    """
    fy_start = fiscal_year_start(period_end, start_month)
    if window == "ptd":
        return fy_start
    if window == "month":
        return max(period_start, fy_start)
    # quarter: the fiscal quarter containing the period end
    months_into_year = (period_end.year - fy_start.year) * 12 + (period_end.month - fy_start.month)
    quarter_offset = months_into_year - (months_into_year % 3)
    year = fy_start.year + (fy_start.month - 1 + quarter_offset) // 12
    month = (fy_start.month - 1 + quarter_offset) % 12 + 1
    return date(year, month, 1)


def month_start(day: date) -> date:
    """Day 1 of the calendar month ``day`` falls in."""
    return day.replace(day=1)


def prior_month_end(day: date) -> date:
    """The last day of the calendar month before ``day``'s."""
    return month_start(day) - timedelta(days=1)


# ---------------------------------------------------------------------------
# the chart-of-accounts → official-item mapping
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


EMPTY_MAPPING = CoaMapping({}, ())


def mapping_rule(row: Mapping[str, Any]) -> MappingRule | None:
    """Normalise one register payload row; ``None`` when it names no item."""
    item = str(row.get("bsd7_item") or "").strip()
    if not item:
        return None
    try:
        sign = Decimal(str(row.get("sign") or "1").strip())
    except ArithmeticError:
        sign = _ONE
    basis_text = str(row.get("balance_basis") or "").strip().lower()
    basis: BalanceBasis | None = basis_text if basis_text in BALANCE_BASES else None
    return MappingRule(item=item, sign=sign, basis=basis)


class PlSignError(ValueError):
    """A register sign the consumer cannot represent without changing its value."""


def register_sign_as_int(sign: Decimal, *, account_code: str, item: str) -> int:
    """``sign`` as an ``int``, refusing anything that would lose precision.

    ``MappingRule.sign`` is an arbitrary ``Decimal`` parsed from bank-supplied
    register data, and BSD7 multiplies it into the filed line exactly as given.
    A consumer with an integral column (``bi_fact_gl_monthly.pl_sign``) must
    therefore either carry it exactly or REFUSE it: ``int(Decimal("0.5"))`` is
    ``0``, which would silently drop the account from every line that sums
    ``pl_sign × ytd_rc`` while the return kept filing it at half weight.

    The register's own schema admits only ``1`` and ``-1``
    (:data:`REGISTER_SIGNS`), so a value outside that set means the row reached
    the ledger without passing ingestion validation — a data fault worth naming
    rather than rounding away.
    """
    if sign not in REGISTER_SIGNS:
        admitted = ", ".join(str(value) for value in REGISTER_SIGNS)
        msg = (
            f"General-ledger account {account_code} maps to BSD7 item {item} with sign "
            f"{sign}, which this projection cannot carry without changing its value. "
            f"The chart-of-accounts register admits {admitted} ({MAPPING_KIND}: '1' "
            "normal, '-1' contra). Correct the register row and re-push it."
        )
        raise PlSignError(msg)
    return int(sign)


def coa_mapping_from_rows(rows: Iterable[Mapping[str, Any]]) -> CoaMapping:
    """The register as a :class:`CoaMapping`: exact codes, then prefixes longest first.

    A row naming both a code and a prefix is an exact-code row (the code wins);
    a later row for the same code or prefix replaces the earlier one.
    """
    codes: dict[str, MappingRule] = {}
    prefixes: dict[str, MappingRule] = {}
    for row in rows:
        rule = mapping_rule(row)
        if rule is None:
            continue
        code = str(row.get("gl_account_code") or "").strip()
        prefix = str(row.get("gl_prefix") or "").strip()
        if code:
            codes[code] = rule
        elif prefix:
            prefixes[prefix] = rule
    return CoaMapping(
        codes=codes,
        prefixes=tuple(sorted(prefixes.items(), key=lambda kv: len(kv[0]), reverse=True)),
    )


def effective_rule(
    account_code: str,
    tag: Any,
    line_tag: str | None,
    mapping: CoaMapping,
    declared_prefixes: Sequence[str],
) -> MappingRule | None:
    """The rule under which ``account_code`` feeds ``line_tag`` — or None when it
    does not: own tag > register exact code > register longest prefix > the line
    map's declared prefixes (default sign/basis)."""
    if tag not in (None, ""):
        selected = line_tag is not None and str(tag) == line_tag
        rule = MappingRule(item=str(tag), sign=_ONE, basis=None) if selected else None
    else:
        rule = mapping.rule_for(account_code) if line_tag else None
        if rule is not None and rule.item != line_tag:
            rule = None
    if rule is None and any(account_code.startswith(p) for p in declared_prefixes):
        rule = MappingRule(item=line_tag or "", sign=_ONE, basis=None)
    return rule


def account_rule(account_code: str, tag: Any, mapping: CoaMapping) -> MappingRule | None:
    """Which official item ``account_code`` feeds, by the bank's own statement.

    The line-agnostic form of :func:`effective_rule` the BI plane needs when it
    projects a whole ledger rather than resolving one line: the account's own
    tag (sign 1, default basis), else the register's rule. ``None`` when the
    bank has mapped it nowhere — the account is still a ledger row, it just
    feeds no P&L line.
    """
    if tag not in (None, ""):
        return MappingRule(item=str(tag), sign=_ONE, basis=None)
    return mapping.rule_for(account_code)


# ---------------------------------------------------------------------------
# generations and the window arithmetic
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Generation:
    """One current-generation ledger row of a selected account."""

    code: str
    as_of: date
    currency: str | None
    balance: Decimal  # already carries the account's mapping sign
    basis: BalanceBasis


def is_base_currency(currency: str | None, base_currency: str) -> bool:
    """A ledger account with no stated currency is a base-currency account."""
    return currency is None or currency == base_currency


def in_currency(rule: CurrencyRule, currency: str | None, base_currency: str) -> bool:
    """Guide §2 per column: Domestic = the bank's base currency; Foreign = any other."""
    if rule == "all":
        return True
    is_base = is_base_currency(currency, base_currency)
    return is_base if rule == "domestic" else not is_base


type InSlice = Callable[[str | None], bool]


def ytd_total(rows: Iterable[Generation], upper: date, *, in_slice: InSlice) -> Decimal:
    """Σ balance of the latest generation per account code with as_of ≤ ``upper``,
    in the currency slice ``in_slice`` admits."""
    latest: dict[str, Generation] = {}
    for row in rows:
        if row.as_of > upper:
            continue
        current = latest.get(row.code)
        if current is None or row.as_of > current.as_of:
            latest[row.code] = row
    return sum((row.balance for row in latest.values() if in_slice(row.currency)), _ZERO)


def period_total(rows: Iterable[Generation], lower: date, *, in_slice: InSlice) -> Decimal:
    """Σ balance of the latest generation per account code per calendar month
    with as_of ≥ ``lower``, in the currency slice — period-movement ledgers.

    A ``period``-basis account is closed monthly, so its balance as of any date
    is the month-to-date movement and the month-end balance is the month's
    movement. A weekly or daily book therefore lands several current-generation
    rows per account per month, each a cumulative figure: only the latest one
    in each month is that month's movement, the earlier ones are superseded by
    it. Summing every generation would count the intra-month movement twice.
    Windows always start on day 1 of a month, so a month never straddles one.
    """
    latest: dict[tuple[str, int, int], Generation] = {}
    for row in rows:
        if row.as_of < lower:
            continue
        key = (row.code, row.as_of.year, row.as_of.month)
        current = latest.get(key)
        if current is None or row.as_of > current.as_of:
            latest[key] = row
    return sum((row.balance for row in latest.values() if in_slice(row.currency)), _ZERO)


def latest_generation(rows: Iterable[Generation], upper: date) -> Generation | None:
    """The latest generation with as_of ≤ ``upper``, or ``None`` when there is none."""
    winner: Generation | None = None
    for row in rows:
        if row.as_of > upper:
            continue
        if winner is None or row.as_of > winner.as_of:
            winner = row
    return winner


def latest_per_month(rows: Iterable[Generation], lower: date, upper: date) -> list[Generation]:
    """One generation per calendar month in ``[lower, upper]``: the latest in each.

    The per-month rule :func:`period_total` applies, returned row by row so a
    caller can attribute each month's movement (the BI monthly mart).
    """
    latest: dict[tuple[int, int], Generation] = {}
    for row in rows:
        if row.as_of < lower or row.as_of > upper:
            continue
        key = (row.as_of.year, row.as_of.month)
        current = latest.get(key)
        if current is None or row.as_of > current.as_of:
            latest[key] = row
    return [latest[key] for key in sorted(latest)]


def line_total(  # noqa: PLR0913 - the window is five explicit dates/rules
    rows: Sequence[Generation],
    *,
    window: Window,
    lower: date,
    upper: date,
    fy_start: date,
    in_slice: InSlice,
    sign: Decimal,
) -> Decimal | None:
    """One official P&L line from its selected generations — BSD7's arithmetic.

    ``rows`` are every current-generation row of the accounts the line selects
    with as_of ∈ [``fy_start``, ``upper``] (all currencies), each carrying its
    account's effective sign and basis. Period-basis rows in the window ARE the
    movement (latest generation per month); YTD-basis rows give the window as
    the difference of period-to-date balances. Returns None when no account is
    selected at all, or when a month/quarter split would need a prior-period
    generation the ledger does not hold; a currency slice with no selected
    account reads 0.
    """
    # period-movement accounts: the window's months (latest generation each) ARE the movement
    period_rows = [row for row in rows if row.basis == PERIOD and row.as_of >= lower]
    ytd_rows = [row for row in rows if row.basis != PERIOD]
    if not period_rows and not ytd_rows:
        return None
    total = period_total(period_rows, lower, in_slice=in_slice)
    if ytd_rows:
        current = ytd_total(ytd_rows, upper, in_slice=in_slice)
        if window == "ptd":
            total += current
        else:
            prior_end = lower - timedelta(days=1)
            if prior_end < fy_start:
                total += current  # first window of the fiscal year: nothing to net off
            elif not any(row.as_of <= prior_end for row in ytd_rows):
                return None  # cannot split the year-to-date figure honestly
            else:
                total += current - ytd_total(ytd_rows, prior_end, in_slice=in_slice)
    return total * sign


# ---------------------------------------------------------------------------
# the monthly account projection (bi_fact_gl_monthly, D-021)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MonthlyAccountFigures:
    """One account's fiscal figures for one calendar month, per the basis above.

    ``ytd`` is the account's period-to-date balance at ``month_end`` (for a
    ``period``-basis account: the sum of its months' movements from the fiscal
    year start); ``prior_ytd`` the same at the prior month's end; ``movement``
    the month. ``missing_prior`` is True only for a ``ytd``-basis account whose
    ledger holds no generation on/before the prior month's end inside the
    fiscal year — the month cannot be split and ``movement`` is ``None``. The
    first month of the fiscal year has no prior by definition (``prior_ytd``
    ``None``, ``movement`` = ``ytd``, ``missing_prior`` False).
    """

    ytd: Decimal
    prior_ytd: Decimal | None
    movement: Decimal | None
    missing_prior: bool


def monthly_account_figures(
    rows: Sequence[Generation], *, month_end: date, fy_start: date, basis: BalanceBasis
) -> MonthlyAccountFigures | None:
    """The account's figures for the month ending ``month_end``.

    ``rows`` are ONE account's generations in ONE currency slice with
    as_of ∈ [``fy_start``, ``month_end``]. ``None`` when the account has no
    generation on/before ``month_end`` in the fiscal year (nothing to state).
    """
    lower = month_start(month_end)
    prior_end = lower - timedelta(days=1)
    first_month = prior_end < fy_start
    if basis == PERIOD:
        months = latest_per_month(rows, fy_start, month_end)
        if not months:
            return None
        movement = sum((row.balance for row in months if row.as_of >= lower), _ZERO)
        ytd = sum((row.balance for row in months), _ZERO)
        prior_ytd = None if first_month else ytd - movement
        return MonthlyAccountFigures(ytd, prior_ytd, movement, False)
    current = latest_generation(rows, month_end)
    if current is None:
        return None
    if first_month:
        return MonthlyAccountFigures(current.balance, None, current.balance, False)
    prior = latest_generation(rows, prior_end)
    if prior is None:
        return MonthlyAccountFigures(current.balance, None, None, True)
    return MonthlyAccountFigures(
        current.balance, prior.balance, current.balance - prior.balance, False
    )
