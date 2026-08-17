"""BSD7B — Current Year Consolidated Results (quarterly P&L, consolidated).

Official layout: one sheet ``BSD7B`` — the BSD7A P&L / appropriation spine
(items 1–37, same labels, two rows lower) in two single columns, *Quarter
Ended* (``C``) and *Period to date* (``D``); no Domestic/Foreign split. Every
total is a template formula; the column-A item numbers are template literals
bound to themselves. Line/cell map: docs/bog_returns/bsd7b_line_map.md.

Basis (Guide §1): subsidiaries' results are consolidated ONLY on BSD7B and
BSD9. The platform holds the parent's P&L ledger; the template has no
separate consolidation-adjustment cell and no "share of subsidiaries' profit"
line — every input cell is one of the 34 P&L / appropriation items. Each P&L
line therefore carries the PARENT'S solo figure from the same ledger mapping
as BSD7A (all currencies; quarter = fiscal quarter to the reporting date;
period to date = fiscal year to date), and the sheet note says so.

Data-gap closure (2026-08-16, documented decision): the ``subsidiaries``
reference dataset (docs/data_engine/datasets/subsidiaries.md) carries each
subsidiary's ``net_profit_ytd_ghs``, but a subsidiary's NET profit cannot be
honestly placed in any single item of this spine (it is the net of its own
interest, fees, expenses, provisions and tax) and adding it to e.g. "Other
Income" would misstate the items and double count once the bank consolidates
line by line — so NO cell is re-pointed here. The subsidiaries' line-by-line
P&L (a subsidiary ledger mapped to the BSD7 items) is the framework ask; until
then the bank adds subsidiaries' results and eliminations before filing.
"""

from __future__ import annotations

from ._common import BANK_COA_MAPPING, INPUT_REQUIRED, RowSource, leaf_lines
from .bsd7a import PL_ROWS, item_numbers

_CONSOLIDATION_NOTE = (
    "parent (solo) figure — subsidiaries' line-by-line results and inter-company eliminations "
    "are not on the platform (the subsidiaries register carries each subsidiary's NET profit "
    "YTD only, which no single P&L item can honestly hold); bank must add them for the "
    "consolidated return"
)

#: BSD7B row = BSD7A row + 2 for the whole P&L spine (items 1–37).
_ROW_OFFSET = 2

_ROWS: dict[int, RowSource] = {
    row + _ROW_OFFSET: RowSource(
        src.source,
        dict(src.params),
        notes=f"{src.notes}; {_CONSOLIDATION_NOTE}" if src.notes else _CONSOLIDATION_NOTE,
        unscaled=src.unscaled,
    )
    for row, src in PL_ROWS.items()
}

LINES = {
    "BSD7B": (
        *leaf_lines(
            "BSD7B",
            "BSD7B",
            value_columns={"quarter": "C", "ptd": "D"},
            row_sources=_ROWS,
            code_prefix="BSD7B",
            default=BANK_COA_MAPPING,
        ),
        *leaf_lines(
            "BSD7B",
            "BSD7B",
            value_columns={"item": "A"},
            row_sources=item_numbers("BSD7B", "BSD7B"),
            code_prefix="BSD7B.ITEM",
            default=INPUT_REQUIRED,
        ),
    ),
}
