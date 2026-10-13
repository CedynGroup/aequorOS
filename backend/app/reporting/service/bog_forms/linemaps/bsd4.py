"""BSD4 — Sectoral Analysis of Overdrafts, Loans and Other Advances.

Official layout: sheet ``BSD4`` (63 sector leaf rows × 10 borrower-class
column groups; each group = PERFORMING ¢ / NON-PERFORMING ¢ / TOTAL (formula)
/ No. of Cust.; the AP:AS grand columns, every section subtotal and the GRAND
TOTAL row are template formulas → 1,890 input cells, 1,498 formulas),
``4a Annexure`` (sectoral distribution by SNA institutional sector — a blank
data grid, C = ¢'Million, D = percentage of total loans, C15 = SUM) and
``4b Annexure`` (geographic distribution — blank grid, C18 = SUM).
Line/cell map: docs/bog_returns/bsd4_line_map.md.

Every leaf row is bound to the ``bsd4.cell`` resolver (sources_ext/bsd4.py):
the row's ``sector`` key + the column key ``"<group>.<measure>"``. Amount
columns are ¢ (scaled to ¢'Million at export); the ``No. of Cust.`` columns are
counts (``unscaled``). The whole sheet is ``input_required`` — with the note
below — until the loan book carries the documented ``sector`` attribute;
nothing is guessed from product codes or names.
"""

from __future__ import annotations

from ..sources_ext.bsd4 import (
    ANNEX_4A_ROWS,
    ANNEX_4B_ROWS,
    BORROWER_GROUPS,
    SECTOR_ROWS,
)
from ._common import RowSource, grid_lines, leaf_lines

_SECTOR_NOTE = (
    "LOAN positions whose documented `sector` attribute (position or counterparty "
    "attributes; key or official leaf label) = {key}; column group from counterparty "
    "type + borrower_class/institution_class/ownership attributes; NPL = IFRS 9 stage 3; "
    "sector classification attribute required — blank until the loan book carries it"
)

#: Amount columns: "<group>.performing" / "<group>.non_performing" → letter.
_AMOUNT_COLUMNS: dict[str, str] = {}
#: Count columns: "<group>.customer_count" → letter (unscaled).
_COUNT_COLUMNS: dict[str, str] = {}
for _group, (_perf, _npl, _count) in BORROWER_GROUPS.items():
    _AMOUNT_COLUMNS[f"{_group}.performing"] = _perf
    _AMOUNT_COLUMNS[f"{_group}.non_performing"] = _npl
    _COUNT_COLUMNS[f"{_group}.customer_count"] = _count


def _sector_row(key: str, *, unscaled: bool) -> RowSource:
    return RowSource(
        "bsd4.cell",
        {"sector": key},
        notes=_SECTOR_NOTE.format(key=key),
        unscaled=unscaled,
    )


_AMOUNT_ROWS: dict[int, RowSource] = {
    row: _sector_row(key, unscaled=False) for row, key in SECTOR_ROWS.items()
}
_COUNT_ROWS: dict[int, RowSource] = {
    row: _sector_row(key, unscaled=True) for row, key in SECTOR_ROWS.items()
}

_ANNEX_4A_NOTE = (
    "all LOAN positions (residents + non-residents) by SNA institutional sector of the "
    "counterparty (Guide Annex 4A notes 2–6): {bucket}; C = ¢, D = % of total loans"
)
_ANNEX_4B_NOTE = (
    "all LOAN positions by counterparty country (ISO 3166-1 α2 → IMF WEO region): "
    "{bucket}; C = ¢, D = % of total loans (Σ top-level rows)"
)

# The annex sheets carry NO input placeholders in the official file (blank
# grids), so their data cells are declared explicitly. Row 9 of 4b ("Regions,
# excluding advanced economies") is a heading and stays unbound.
_ANNEX_COLUMNS_AMOUNT = {"amount": "C"}
_ANNEX_COLUMNS_SHARE = {"share": "D"}


def _annex_rows(
    resolver: str, buckets: dict[int, str], note: str, *, unscaled: bool
) -> dict[int, RowSource]:
    return {
        row: RowSource(
            resolver, {"bucket": bucket}, notes=note.format(bucket=bucket), unscaled=unscaled
        )
        for row, bucket in buckets.items()
    }


LINES = {
    "BSD4": (
        leaf_lines(
            "BSD4",
            "BSD4",
            value_columns=_AMOUNT_COLUMNS,
            row_sources=_AMOUNT_ROWS,
            code_prefix="BSD4",
        )
        + leaf_lines(
            "BSD4",
            "BSD4",
            value_columns=_COUNT_COLUMNS,
            row_sources=_COUNT_ROWS,
            code_prefix="BSD4.CUST",
        )
    ),
    "4a Annexure": (
        grid_lines(
            "BSD4",
            "4a Annexure",
            rows=tuple(ANNEX_4A_ROWS),
            value_columns=_ANNEX_COLUMNS_AMOUNT,
            row_sources=_annex_rows("bsd4.annex4a", ANNEX_4A_ROWS, _ANNEX_4A_NOTE, unscaled=False),
            code_prefix="BSD4.4A",
        )
        + grid_lines(
            "BSD4",
            "4a Annexure",
            rows=tuple(ANNEX_4A_ROWS),
            value_columns=_ANNEX_COLUMNS_SHARE,
            row_sources=_annex_rows("bsd4.annex4a", ANNEX_4A_ROWS, _ANNEX_4A_NOTE, unscaled=True),
            code_prefix="BSD4.4A.PCT",
        )
    ),
    "4b Annexure": (
        grid_lines(
            "BSD4",
            "4b Annexure",
            rows=tuple(ANNEX_4B_ROWS),
            value_columns=_ANNEX_COLUMNS_AMOUNT,
            row_sources=_annex_rows("bsd4.annex4b", ANNEX_4B_ROWS, _ANNEX_4B_NOTE, unscaled=False),
            code_prefix="BSD4.4B",
        )
        + grid_lines(
            "BSD4",
            "4b Annexure",
            rows=tuple(ANNEX_4B_ROWS),
            value_columns=_ANNEX_COLUMNS_SHARE,
            row_sources=_annex_rows("bsd4.annex4b", ANNEX_4B_ROWS, _ANNEX_4B_NOTE, unscaled=True),
            code_prefix="BSD4.4B.PCT",
        )
    ),
}
