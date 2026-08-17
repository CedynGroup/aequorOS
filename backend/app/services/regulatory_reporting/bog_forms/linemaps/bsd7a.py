"""BSD7A — Current Year Results (monthly P&L, solo).

Official layout: one sheet ``BSD7A`` — items 1–37 are the P&L / appropriation
spine in two column blocks, *Month-ended* (Domestic ``C`` / Foreign ``D`` /
Total ``E`` = formula) and *Period to date* (``F`` / ``G`` / ``H`` = formula);
items 38–42 are period averages (``C`` = Average Quarter Ended, ``D`` = Average
Period to date); items 43–51 are staff numbers. Every total (1, 2, 2a, 3, 11,
19, 23, 27, 29, 31, 33, 37, 46, 50, 51) is a template formula. The item numbers
in column ``A`` are numeric template literals captured as input cells; they are
bound to their own value so the export carries the official numbering.
Line/cell map: docs/bog_returns/bsd7a_line_map.md.

Sources. The platform has no chart of accounts of its own: P&L lines resolve
from the bank's INCOME/EXPENSE general-ledger accounts tagged with the official
item (``attributes.bsd7_line``, see ``sources_ext/bsd7.py``); the fiscal-year-
to-date ledger balance feeds *Period to date* and month = difference of
consecutive period-to-date balances. Untagged lines stay ``input_required``
until the bank's CoA mapping names them — never a guessed split. The averages
block reads the platform's own balance-sheet / capital facts (month-end mean).
"""

from __future__ import annotations

from ..layout import load_layout
from ._common import BANK_COA_MAPPING, INPUT_REQUIRED, RowSource, leaf_lines

_INCOME = ["INCOME"]
_EXPENSE = ["EXPENSE"]

_COA_NOTE = (
    "from the bank's P&L ledger: GL accounts tagged attributes.bsd7_line={tag} "
    "(or account_code_prefixes in the bank's CoA mapping); blank until tagged"
)


def pl(tag: str, *, classes: list[str] | None, extra: str = "") -> RowSource:
    params: dict[str, object] = {"line": tag}
    if classes is not None:
        params["gl_classes"] = classes
    note = _COA_NOTE.format(tag=tag)
    if extra:
        note = f"{note}; {extra}"
    return RowSource("bsd7.pl_line", params, notes=note)


#: BSD7A row → source. Item numbers are the template's; the tag is the item.
PL_ROWS: dict[int, RowSource] = {
    # 1. Interest received and receivable
    6: pl("1a", classes=_INCOME),
    7: pl("1b", classes=_INCOME),
    8: pl("1c", classes=_INCOME),
    # 2. Interest paid and payable — (a) on deposits by account type, (b) other
    11: pl("2a_savings", classes=_EXPENSE),
    12: pl("2a_current", classes=_EXPENSE),
    13: pl("2a_time", classes=_EXPENSE),
    14: pl("2a_borrowings", classes=_EXPENSE),
    15: pl("2b", classes=_EXPENSE),
    # 4–10 other income
    17: pl("4", classes=_INCOME, extra="FX dealing profits (losses go to item 26)"),
    18: pl("5", classes=_INCOME),
    19: pl("6", classes=_INCOME),
    20: pl("7", classes=None, extra="net gain/loss on disposal — signed per the ledger"),
    21: pl("8", classes=_INCOME),
    22: pl("9", classes=_INCOME, extra="dealing gains (losses go to item 25)"),
    23: pl("10", classes=_INCOME),
    # 12–18 operating expenses by class
    25: pl("12", classes=_EXPENSE),
    26: pl("13", classes=_EXPENSE),
    27: pl("14", classes=_EXPENSE),
    28: pl("15", classes=_EXPENSE),
    29: pl("16", classes=_EXPENSE),
    30: pl("17", classes=_EXPENSE),
    31: pl("18", classes=_EXPENSE),
    # 20–22 provisions
    33: pl("20", classes=_EXPENSE),
    34: pl("21", classes=_EXPENSE, extra="charge for the period, not the provision stock"),
    35: pl("22", classes=_EXPENSE),
    # 24–26 losses
    37: pl("24", classes=_EXPENSE),
    38: pl("25", classes=_EXPENSE),
    39: pl("26", classes=_EXPENSE),
    # 28 tax, 30 extraordinary, 32 dividends
    41: pl("28", classes=_EXPENSE),
    43: pl("30", classes=None, extra="signed net; the template deducts it (charge positive)"),
    45: pl("32", classes=None, extra="dividends declared/paid in the period (appropriation)"),
    # 34–36 reserves — the template sums these WITH retained profit into TOTAL
    # RESERVES, i.e. reserve balances before the period's appropriation; the
    # platform's capital_component facts are period-end balances that may
    # already include the period's profit, so no honest source exists.
    47: RowSource(
        None,
        notes="statutory reserve fund balance before this period's appropriation — bank must "
        "supply from its appropriation account",
    ),
    48: RowSource(
        None,
        notes="other reserves balance before this period's appropriation — bank must supply",
    ),
    49: RowSource(
        None,
        notes="income surplus brought forward before this period's appropriation — bank must "
        "supply",
    ),
}

_AVERAGE_ROWS: dict[int, RowSource] = {
    54: RowSource(
        "bsd7.average_facts",
        {"group": "balance_sheet", "attribute_eq": {"side": "asset"}},
        notes="mean of month-end total assets (Σ balance_sheet asset facts) over the fiscal "
        "quarter / year to date",
    ),
    55: RowSource(
        None,
        notes="property, plant & equipment sits inside the platform's other_assets residual — "
        "fixed-asset register / CoA mapping required for the average",
    ),
    56: RowSource(
        "bsd7.average_facts",
        {"group": "capital_component", "capital_tiers": ["CET1"], "exclude_deductions": True},
        notes="mean of month-end shareholders' funds (paid-up capital + reserves = CET1 "
        "components before regulatory deductions) over the fiscal quarter / year to date",
    ),
    57: RowSource(
        None,
        notes="interest-earning classification of assets is not a platform attribute — bank "
        "must supply the average",
    ),
    58: RowSource(
        None,
        notes="interest-bearing classification of liabilities is not a platform attribute — "
        "bank must supply the average",
    ),
}

_STAFF_ROWS: dict[int, RowSource] = {
    row: RowSource(
        None, notes="headcount by grade and location — HR register required", unscaled=True
    )
    for row in (63, 64, 65, 68, 69, 70)
}


def item_numbers(form: str, sheet: str) -> dict[int, RowSource]:
    """Column-A item numbers: template literals captured as inputs → bound to themselves."""
    layout = load_layout(form).sheet(sheet)
    return {
        cell.row: RowSource(
            "constant",
            {"value": cell.value},
            notes="official item number (template literal)",
            unscaled=True,
        )
        for cell in layout.input_cells
        if cell.ref.startswith("A") and cell.ref[1:].isdigit()
    }


LINES = {
    "BSD7A": (
        *leaf_lines(
            "BSD7A",
            "BSD7A",
            value_columns={
                "month_domestic": "C",
                "month_foreign": "D",
                "ptd_domestic": "F",
                "ptd_foreign": "G",
            },
            row_sources=PL_ROWS,
            code_prefix="BSD7A",
            default=BANK_COA_MAPPING,
            only_rows=range(5, 51),
        ),
        *leaf_lines(
            "BSD7A",
            "BSD7A",
            value_columns={"quarter": "C", "ptd": "D"},
            row_sources=_AVERAGE_ROWS,
            code_prefix="BSD7A",
            default=INPUT_REQUIRED,
            only_rows=range(52, 60),
        ),
        *leaf_lines(
            "BSD7A",
            "BSD7A",
            value_columns={"quarter": "C", "ptd": "D"},
            row_sources=_STAFF_ROWS,
            code_prefix="BSD7A",
            default=INPUT_REQUIRED,
            only_rows=range(60, 75),
        ),
        *leaf_lines(
            "BSD7A",
            "BSD7A",
            value_columns={"item": "A"},
            row_sources=item_numbers("BSD7A", "BSD7A"),
            code_prefix="BSD7A.ITEM",
            default=INPUT_REQUIRED,
        ),
    ),
}
