"""BSD14 — Weekly Return on Interest Rates (``INTEREST&LENDING-RATES``).

Official workbook ``FORM BSD14 REVISED.xls``, one sheet, all percentages:

* ``BASE RATE:`` (A9 → value cell B9);
* the rate grid — rows ``Cedis`` (17), ``USD`` (28), ``GBP`` (29), ``DEM``
  (30), ``All other Currencies`` (31) × columns B..U: BORROWING RATES —
  Demand deposit (B), Savings deposit (C), Fixed/Time deposits by tenor 1 · 2
  · 3 · 6 · 12 · 24 · 36 months (D..J — the tenor headers D14:J14 are the
  template's only captured input cells and are kept as constants),
  Certificate of deposits (K), Call deposit (L), Any other (M); LENDING RATES
  by sector — Agriculture (N), Exports (O), Mining/Quarrying (P),
  Manufacturing (Q), Construction (R), Imports (S), Commerce (T), Others (U).

The template ships the grid EMPTY, so :func:`_common.grid_lines` names the
100 official rate cells; the spare unlabelled rows (18–25, 32–43) are the
form's "add other items" space and are not official lines. Every rate cell
binds ``bsd14.rate`` (see ``sources_ext/bsd14.py``): the balance-weighted
average contractual ``interest_rate`` of the matching DEPOSIT / LOAN
positions in that currency, as a percent — ``input_required`` ("product rate
table required") wherever the book has no such position or no rate. The BASE
RATE is the bank's declared figure and is ``input_required``.

Line/cell map: docs/bog_returns/bsd14_line_map.md.
"""

from __future__ import annotations

from typing import Any

from ._common import RowSource, grid_lines, leaf_lines

FORM = "BSD14"
SHEET = "INTEREST&LENDING-RATES"

#: Column key → official column letter (the resolver reads the product from
#: the column key; see ``sources_ext.bsd14.COLUMN_PRODUCTS``).
RATE_COLUMNS: dict[str, str] = {
    "demand": "B",
    "savings": "C",
    "td_1": "D",
    "td_2": "E",
    "td_3": "F",
    "td_6": "G",
    "td_12": "H",
    "td_24": "I",
    "td_36": "J",
    "cd": "K",
    "call": "L",
    "other_deposit": "M",
    "agriculture": "N",
    "exports": "O",
    "mining": "P",
    "manufacturing": "Q",
    "construction": "R",
    "imports": "S",
    "commerce": "T",
    "others": "U",
}
#: Official currency rows.
CURRENCY_ROWS: dict[int, str] = {17: "GHS", 28: "USD", 29: "GBP", 30: "DEM", 31: "other"}
BASE_RATE_ROW = 9
TENOR_HEADER_ROW = 14
_TENOR_HEADERS: dict[str, int] = {"D": 1, "E": 2, "F": 3, "G": 6, "H": 12, "I": 24, "J": 36}


def rate(currency: str, **params: Any) -> RowSource:
    """Balance-weighted average offered rate (percent) for the row's currency;
    the product comes from the column key. ``currency="GHS"`` binds the Cedis
    row to the bank's base currency at resolve time."""
    return RowSource(
        "bsd14.rate",
        {"currency": currency, **params},
        notes=(
            "balance-weighted average contractual interest_rate of matching positions "
            "(percent); input_required where no position carries a rate — product rate "
            "table required"
        ),
        unscaled=True,
    )


_ROWS: dict[int, RowSource] = {row: rate(currency) for row, currency in CURRENCY_ROWS.items()}
_BASE_RATE = RowSource(
    None,
    notes=(
        "the bank's published BASE RATE (a declared benchmark, not derivable from "
        "positions) — bank must supply"
    ),
)

LINES = {
    SHEET: (
        *grid_lines(
            FORM,
            SHEET,
            rows=[BASE_RATE_ROW],
            value_columns={"base_rate": "B"},
            row_sources={BASE_RATE_ROW: _BASE_RATE},
            code_prefix="BSD14.BASE_RATE",
            label_column="A",
        ),
        # The tenor headers (1 · 2 · 3 · 6 · 12 · 24 · 36 months) are the only
        # input cells the template captures — kept verbatim as constants.
        *leaf_lines(
            FORM,
            SHEET,
            value_columns={f"td_{months}": letter for letter, months in _TENOR_HEADERS.items()},
            row_sources={
                TENOR_HEADER_ROW: RowSource(
                    "bsd14.column_constant",
                    {"values": {f"td_{months}": months for months in _TENOR_HEADERS.values()}},
                    notes="template tenor header (months)",
                    unscaled=True,
                )
            },
            code_prefix="BSD14.TENOR_HEADER",
            only_rows=[TENOR_HEADER_ROW],
        ),
        *grid_lines(
            FORM,
            SHEET,
            rows=list(CURRENCY_ROWS),
            value_columns=RATE_COLUMNS,
            row_sources=_ROWS,
            code_prefix="BSD14.RATE",
            label_column="A",
        ),
    ),
}
