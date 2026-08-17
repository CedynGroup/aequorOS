"""BSD1A (Twenty Largest Withdrawals Over the Counter) resolver.

The official sheet is a ranked customer × weekday matrix: rows 11–30 carry
CUSTOMER / BRANCH / TYPE OF A/C and one amount column per template day
(THURSDAY … WEDNESDAY, ¢ Million), with ``J = SUM(E:I)`` per row and ``J31``
the grand total (template formulas). Its source is the ``teller_withdrawals``
reference dataset (docs/data_engine/datasets/teller_withdrawals.md — one row per
over-the-counter cash withdrawal, pushed one week per batch through the Data
Engine).

Ranking rule — the template's, not a new one: the sheet gives every ranked row
one cell per weekday AND a weekly TOTAL, so a row is one customer account
(customer × branch × account type) and its five day cells are that account's
cash withdrawals on each day of the reporting week; the twenty rows are the
twenty largest weekly totals, largest first. A customer who withdrew once
appears with one day filled and the others ``0`` (a true zero: nothing was
withdrawn that day). The week is the seven days ending on the reporting date;
rows dated outside it are ignored (a bank pushing two weeks in one file cannot
leak the earlier week into the return). Saturday / Sunday withdrawals have no
column on the official sheet and are therefore not ranked (documented in the
line map — a treatment BoG must confirm).

Nothing here computes a BoG figure by a new rule: the amounts are the bank's
own ``amount_ghs`` per transaction, summed per account per day and per week.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from ..sources import ResolveContext, reference_rows, resolver

KIND = "teller_withdrawals"
#: Column key → ``date.weekday()`` (Monday = 0). The official sheet has no
#: Saturday / Sunday columns.
DAY_COLUMNS: dict[str, int] = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4}
TEXT_COLUMNS: tuple[str, ...] = ("customer", "branch", "account_type")
WEEK_DAYS = 7
_ZERO = Decimal(0)


@dataclass
class RankedAccount:
    """One ranked row: an account's over-the-counter withdrawals for the week."""

    customer: str
    branch: str
    account_type: str
    by_weekday: dict[int, Decimal] = field(default_factory=dict)
    total: Decimal = _ZERO
    transactions: int = 0


def week_window(period_end: date) -> tuple[date, date]:
    """The reporting week: the seven days ending on the reporting date."""
    return period_end - timedelta(days=WEEK_DAYS - 1), period_end


def _parse_date(raw: Any) -> date | None:
    if raw in (None, ""):
        return None
    try:
        return date.fromisoformat(str(raw).strip()[:10])
    except ValueError:
        return None


def _parse_amount(raw: Any) -> Decimal | None:
    """The row's cedi amount as ingested (reference payloads are stringified)."""
    if raw in (None, ""):
        return None
    try:
        return Decimal(str(raw).replace(",", "").strip())
    except (ArithmeticError, ValueError):
        return None


def ranked_accounts(rc: ResolveContext) -> list[RankedAccount] | None:
    """The week's accounts ranked by weekly cedi total (largest first);
    ``None`` when the dataset was never ingested. Memoised per form."""
    key = "bsd1a:ranked"
    if key in rc.cache:
        return rc.cache[key]
    rows = reference_rows(rc, KIND)
    if not rows:
        rc.cache[key] = None
        return None
    start, end = week_window(rc.period.period_end)
    groups: dict[tuple[str, str, str], RankedAccount] = {}
    for row in rows:
        day = _parse_date(row.get("txn_date"))
        if day is None or day < start or day > end or day.weekday() not in DAY_COLUMNS.values():
            continue
        amount = _parse_amount(row.get("amount_ghs"))
        if amount is None:
            continue  # unparseable amount: reported by the batch validator, never guessed here
        customer = str(row.get("customer_name") or row.get("customer_reference") or "").strip()
        reference = str(row.get("customer_reference") or customer).strip()
        branch = str(row.get("branch") or "").strip()
        account_type = str(row.get("account_type") or "").strip()
        group_key = (reference, branch, account_type)
        group = groups.get(group_key)
        if group is None:
            group = RankedAccount(customer=customer, branch=branch, account_type=account_type)
            groups[group_key] = group
        group.by_weekday[day.weekday()] = group.by_weekday.get(day.weekday(), _ZERO) + amount
        group.total += amount
        group.transactions += 1
    ranked = sorted(
        groups.values(), key=lambda g: (-g.total, g.customer, g.branch, g.account_type)
    )
    rc.cache[key] = ranked
    return ranked


@resolver("bsd1a.rank")
def _rank(rc: ResolveContext, params: dict[str, Any]) -> Decimal | str | None:  # noqa: PLR0911 — one return per official column
    """Cell of the ``rank``-th (1-based) largest weekly over-the-counter
    withdrawer, selected by the bound column: ``customer`` / ``branch`` /
    ``account_type`` (text) or a template day (``thu`` … ``wed``: that day's
    cedi total, ``0`` when the account withdrew nothing that day).

    ``None`` (⇒ ``input_required``) when the dataset was never ingested for the
    period, or when the week has fewer ranked accounts than ``rank`` (the
    official row is then genuinely blank — the line note says so)."""
    ranked = ranked_accounts(rc)
    if ranked is None:
        return None
    index = int(params["rank"]) - 1
    if index < 0 or index >= len(ranked):
        return None
    account = ranked[index]
    column = rc.column
    if column == "customer":
        return account.customer
    if column == "branch":
        return account.branch
    if column == "account_type":
        return account.account_type.replace("_", " ").title()
    weekday = DAY_COLUMNS.get(column)
    if weekday is None:
        return None
    return account.by_weekday.get(weekday, _ZERO)
