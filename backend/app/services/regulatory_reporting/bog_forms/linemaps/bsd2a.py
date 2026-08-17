"""BSD2A — Monthly Report on Foreign Currency Exposures.

Official layout: one sheet, ``FOREIGN CURRENCY EXPOSURES`` — a BLANK grid
(no numeric placeholders, so the layout captured no input cells) of 14 columns
(A name of bank/institution · B type of exposure · C currency · D foreign
currency amount · E cedi equivalent · F exchange conversion rate · G net worth
of reporting bank · H %age of exposure to net worth · I maturity date ·
J interest rate · K rating of counterparty · L net worth of counterparty
(USD M) · M date of audit report · N provision) under five section headers
(A. foreign assets · B. domestic assets · C. foreign liabilities · D. domestic
liabilities · E. contingent liabilities). Line/cell map:
docs/bog_returns/bsd2a_line_map.md.

Guide BSD2A ¶1: every item shown in the FOREIGN column of BSD2 is analysed
here — so each CATEGORY row (the labelled item rows) carries, in the cedi
column, the BSD2 foreign-column figure of the line it names (``form.cell`` /
``bsd2a.form_cells_sum`` over the computed BSD2 of the same reporting date —
"the total group exposure ... in the cedis column", ¶3(c)), the reporting
bank's net worth (BSD2 16 Shareholders' Funds, ¶5(vi)) and the exposure /
net-worth percentage (¶5(vii)); the loans row also carries the BSD2 debt
provision (¶3(d)). The blank DETAIL rows beneath each category are the
per-counterparty / per-currency schedule (¶3(a)–(c), ¶5, ¶6) — bound
``input_required`` on every column until position-level data feeds them.
Section headers and their spacer rows are not data rows and are not bound;
rows 106–127 carry no labels and are treated as outside the grid.

Category rows whose BSD2 counterpart is a judgement rather than a line
(E. contingent liabilities — Guide ¶7 restricts to commitments "certain to be
called upon and likely to be irrecoverable"; "(ii) Inoperative" demand
deposits — no dormancy flag in canonical data) stay ``input_required``.
"""

from __future__ import annotations

from ..spec import LineSpec
from ._common import RowSource, grid_lines

SHEET = "FOREIGN CURRENCY EXPOSURES"

#: BSD2A category row → the BSD2 FOREIGN-column cell(s) it analyses (Guide ¶1).
#: A tuple of several refs is summed (``bsd2a.form_cells_sum``).
BSD2_FOREIGN_CELLS: dict[int, tuple[str, ...]] = {
    # A. FOREIGN ASSETS
    13: ("C7",),  # (a) foreign currency notes and coins            ← BSD2 A.1
    15: ("C8",),  # (b) correspondent acc. in non-res. fin. inst.   ← BSD2 A.2
    17: ("C9",),  # (c) other claims on non-residents               ← BSD2 A.3
    # B. DOMESTIC ASSETS
    22: ("C15",),  # (a) claims on central banks — Bank of Ghana    ← BSD2 6(b)
    24: ("C21",),  # (b) claims on other banks — BSD2 6(c) other depository institutions
    27: ("C68",),  # (c) loans, overdrafts and other advances (gross) ← BSD2 8 sub-total
    30: ("C34", "C72", "C102"),  # (d) securities ← BSD2 7 bills + 9 long-term + 10 shares
    39: ("C113",),  # (e) other assets                               ← BSD2 11
    # C. FOREIGN LIABILITIES
    44: ("C138",),  # (a) short-term borrowings                      ← BSD2 18
    47: ("C142",),  # (b) long-term borrowing                        ← BSD2 19
    49: ("C146",),  # (c) deposits of non-residents                  ← BSD2 20
    # D. DOMESTIC LIABILITIES
    54: ("C168",),  # (a) long-term borrowings                       ← BSD2 21
    56: ("C184",),  # (b) short-term borrowing                       ← BSD2 23
    58: ("C178",),  # (c) cheques for clearing                       ← BSD2 22
    60: ("C196",),  # (d) deposits of financial institutions         ← BSD2 24
    66: ("C228", "C233"),  # (e)(1)(i) demand deposits — individuals & others ← BSD2 25(a)(i)+(vi)
    77: ("C244",),  # (e)(ii) time deposits — individual             ← BSD2 25(c)(i)
    80: ("C236",),  # (e)(iii) savings — individual                  ← BSD2 25(b)(i)
    82: ("C259",),  # (f) special deposits                           ← BSD2 26
    84: ("C274",),  # (g) margins against contingent liabilities     ← BSD2 27
    90: ("C278",),  # (h) other liabilities                          ← BSD2 29
}

#: Category rows with no honest BSD2 counterpart (bank judgement / no flag).
JUDGEMENT_ROWS: dict[int, str] = {
    71: (
        "inoperative (dormant) demand deposits — no dormancy flag in canonical data; the "
        "whole of BSD2 25(a)(i)+(vi) is shown on (i) until one exists; bank must supply"
    ),
    99: (
        "customers' liabilities (contingent) — Guide BSD2A ¶7: report only foreign-currency "
        "commitments certain to be called upon and likely irrecoverable (bank judgement; "
        "BSD2 33 foreign column is the ceiling)"
    ),
    103: (
        "bonds & guarantees (contingent) — Guide BSD2A ¶7: report only foreign-currency "
        "commitments certain to be called upon and likely irrecoverable (bank judgement; "
        "BSD2 33 foreign column is the ceiling)"
    ),
}

CATEGORY_ROWS: tuple[int, ...] = tuple(sorted({*BSD2_FOREIGN_CELLS, *JUDGEMENT_ROWS}))

#: Section headers + the blank spacer row that follows each, and group
#: headings whose figure sits on a named sub-row — never data rows.
_HEADING_ROWS: frozenset[int] = frozenset(
    {11, 12, 19, 20, 21, 42, 43, 52, 53, 62, 63, 64, 65, 76, 79, 97, 98}
)
_GRID_FIRST, _GRID_LAST = 13, 105
DETAIL_ROWS: tuple[int, ...] = tuple(
    row
    for row in range(_GRID_FIRST, _GRID_LAST + 1)
    if row not in _HEADING_ROWS and row not in CATEGORY_ROWS
)

_NET_WORTH_REF = "D135"  # BSD2 16. Shareholders' Funds (TOTAL column) — Guide ¶5(vi)

_DETAIL_COLUMNS: dict[str, str] = {
    "name": "A",
    "type_of_exposure": "B",
    "currency": "C",
    "fcy_amount": "D",
    "cedi_equivalent": "E",
    "exchange_rate": "F",
    "net_worth_of_bank": "G",
    "pct_of_net_worth": "H",
    "maturity_date": "I",
    "interest_rate": "J",
    "counterparty_rating": "K",
    "counterparty_net_worth": "L",
    "audit_report_date": "M",
    "provision": "N",
}


def _cedi_source(row: int) -> RowSource:
    if row in JUDGEMENT_ROWS:
        return RowSource(None, notes=JUDGEMENT_ROWS[row])
    refs = BSD2_FOREIGN_CELLS[row]
    if len(refs) == 1:
        return RowSource(
            "form.cell",
            {"form": "BSD2", "sheet": "BSD2", "ref": refs[0]},
            notes=f"= BSD2 {refs[0]} (foreign column) — Guide BSD2A ¶1/¶3(c)",
        )
    return RowSource(
        "bsd2a.form_cells_sum",
        {"form": "BSD2", "sheet": "BSD2", "refs": list(refs)},
        notes=f"= BSD2 {' + '.join(refs)} (foreign column) — Guide BSD2A ¶1/¶3(c)",
    )


def _pct_source(row: int) -> RowSource:
    if row in JUDGEMENT_ROWS:
        return RowSource(
            None,
            notes="% of exposure to net worth — follows the cedi equivalent (input_required)",
            unscaled=True,
        )
    return RowSource(
        "bsd2a.form_cells_ratio_pct",
        {
            "form": "BSD2",
            "sheet": "BSD2",
            "numerator": list(BSD2_FOREIGN_CELLS[row]),
            "denominator": [_NET_WORTH_REF],
        },
        notes="100 × cedi equivalent / BSD2 16 shareholders' funds — Guide BSD2A ¶5(vii)",
        unscaled=True,
    )


def _provision_source(row: int) -> RowSource:
    if row == 27:  # the loans category row (PLR2004 is ignored repo-wide)
        return RowSource(
            "form.cell",
            {"form": "BSD2", "sheet": "BSD2", "ref": "C69"},
            notes="= BSD2 C69 total debt provision (foreign column) — Guide BSD2A ¶3(d)",
        )
    return RowSource(
        None, notes="provisions booked against exposures in this category — bank must supply"
    )


_NET_WORTH = RowSource(
    "form.cell",
    {"form": "BSD2", "sheet": "BSD2", "ref": _NET_WORTH_REF},
    notes="net worth of reporting bank = BSD2 16 Shareholders' Funds — Guide BSD2A ¶5(vi)",
)


def _grid(
    rows: tuple[int, ...],
    columns: dict[str, str],
    sources: dict[int, RowSource],
    prefix: str,
) -> tuple[LineSpec, ...]:
    return grid_lines(
        "BSD2A", SHEET, rows=rows, value_columns=columns, row_sources=sources, code_prefix=prefix
    )


LINES: dict[str, tuple[LineSpec, ...]] = {
    SHEET: (
        *_grid(
            CATEGORY_ROWS,
            {"cedi_equivalent": "E"},
            {row: _cedi_source(row) for row in CATEGORY_ROWS},
            "BSD2A.cedi",
        ),
        *_grid(
            CATEGORY_ROWS,
            {"net_worth_of_bank": "G"},
            dict.fromkeys(CATEGORY_ROWS, _NET_WORTH),
            "BSD2A.networth",
        ),
        *_grid(
            CATEGORY_ROWS,
            {"pct_of_net_worth": "H"},
            {row: _pct_source(row) for row in CATEGORY_ROWS},
            "BSD2A.pct",
        ),
        *_grid(
            CATEGORY_ROWS,
            {"provision": "N"},
            {row: _provision_source(row) for row in CATEGORY_ROWS},
            "BSD2A.provision",
        ),
        *_grid(
            DETAIL_ROWS,
            _DETAIL_COLUMNS,
            {
                row: RowSource(
                    None,
                    notes=f"detail schedule row {row} — per-counterparty / per-currency exposure "
                    "(Guide BSD2A ¶3–¶6) populated from position-level data in a later wave",
                )
                for row in DETAIL_ROWS
            },
            "BSD2A.detail",
        ),
    )
}
