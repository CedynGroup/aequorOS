"""``gl_mapping_bsd7`` — the bank's chart-of-accounts → BSD7A/BSD7B P&L item
mapping (feeds every P&L line of BSD7A *Current Year Results* and BSD7B
*Consolidated Results*; BSD11 depends on BSD7A).

The platform has no chart of accounts of its own: which INCOME/EXPENSE ledger
accounts feed which official P&L item is the bank's decision. This register
states it once, as data — one row per ``gl_account_code`` (exact) or
``gl_prefix`` (every account whose code starts with it), naming the official
item (``bsd7_item``, the vocabulary below), the sign the account contributes
with (``1`` normal, ``-1`` contra) and the ledger's balance basis (``ytd``
fiscal-year-to-date trial-balance balances — the default; ``period`` when the
ledger delivers each month's movement instead).

Precedence when the P&L resolver (``bog_forms/sources_ext/bsd7.py::bsd7.pl_line``)
selects accounts for a line: (1) an account's own ``attributes.bsd7_line`` tag
(ingested with the ledger) always wins; (2) else an exact ``gl_account_code``
row; (3) else the LONGEST matching ``gl_prefix`` row; (4) else the line map's
own ``account_code_prefixes``. A register row can therefore never re-route an
account the ledger itself tagged.

It is a register, not a periodic series: push it once (any ``as_of_date``) and
re-push the whole table when the mapping changes — the resolver reads the
latest as-of on/before the reporting date.
"""

from __future__ import annotations

from . import ReferenceSchema, register

#: Official BSD7A/BSD7B item tags the P&L line map binds (item numbers of the
#: official form; ``2a_*`` are the four "(a) on deposits" sub-lines of item 2).
#: Kept in step with ``bog_forms/linemaps/bsd7a.py::PL_ROWS`` by
#: ``tests/services/data_gaps/test_gl_mapping_bsd7.py``.
BSD7_ITEMS: tuple[str, ...] = (
    "1a",  # interest received — overdrafts, loans & other advances
    "1b",  # interest received — bills (including discounts)
    "1c",  # interest received — investments (including discounts)
    "2a_savings",  # interest paid — savings deposits
    "2a_current",  # interest paid — current deposits
    "2a_time",  # interest paid — time deposits
    "2a_borrowings",  # interest paid — borrowings
    "2b",  # other interest payments
    "4",  # profit on foreign exchange dealings
    "5",  # fees and commissions
    "6",  # dividends received
    "7",  # profit/loss on sale of property, plant and equipment (signed)
    "8",  # rent receivable
    "9",  # gain on dealing assets
    "10",  # other income
    "12",  # operating expense — staff
    "13",  # — training
    "14",  # — emoluments
    "15",  # — other staff costs
    "16",  # — occupancy
    "17",  # — travel
    "18",  # — admin & other
    "20",  # provisions — depreciation
    "21",  # provisions — bad debts (charge for the period)
    "22",  # provisions — other
    "24",  # losses on sale of investment
    "25",  # losses on dealing assets
    "26",  # exchange losses
    "28",  # provision for taxation
    "30",  # extraordinary items (signed)
    "32",  # dividends paid and payable
)

SIGNS: tuple[str, ...] = ("1", "-1")
BALANCE_BASES: tuple[str, ...] = ("ytd", "period")

SCHEMA = register(
    ReferenceSchema(
        kind="gl_mapping_bsd7",
        description=(
            "Bank chart-of-accounts → BSD7A/BSD7B P&L item mapping: one row per GL account "
            "code or code prefix naming the official item it feeds, its sign and balance basis"
        ),
        grain=(
            "one row per gl_account_code (exact) or gl_prefix (starts-with); a register, "
            "re-pushed whole when the mapping changes (latest as_of wins)"
        ),
        required=("bsd7_item",),
        optional=(
            "gl_account_code",
            "gl_prefix",
            "sign",
            "balance_basis",
            "gl_account_name",
            "notes",
        ),
        enums={"bsd7_item": BSD7_ITEMS, "sign": SIGNS, "balance_basis": BALANCE_BASES},
    )
)


def validate_mapping_row(row: dict) -> list[str]:
    """Schema problems plus the one cross-field rule: exactly one selector."""
    problems = SCHEMA.validate_row(row)
    code = str(row.get("gl_account_code") or "").strip()
    prefix = str(row.get("gl_prefix") or "").strip()
    if not code and not prefix:
        problems.append("one of 'gl_account_code' or 'gl_prefix' is required")
    elif code and prefix:
        problems.append("give either 'gl_account_code' or 'gl_prefix', not both")
    return problems
