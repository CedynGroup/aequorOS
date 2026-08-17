"""BSD9 — Consolidated Balance Sheet (quarterly, consolidated).

Official layout: sheet ``BSD9`` (31 leaf rows: label / DOMESTIC / FOREIGN /
TOTAL = formula; Total Assets, Loans (net), Shareholders' funds and Total
Liabilities are template formulas), ``Annexure`` (Details of inter-company
transactions — a blank DATE / TYPE / SUBSIDIARY / AMOUNT grid, rows 11–28) and
the empty placeholder ``Sheet3``. Line/cell map: docs/bog_returns/bsd9_line_map.md.

BSD9 is the condensed BSD2: every BSD9 line is one BSD2 line or a roll-up of
several, in the same Domestic / Foreign columns, so each row binds
``bsd9.bsd2_lines`` over the corresponding BSD2 rows (BSD2 is computed first —
``depends_on``); BoG's BSD2 arithmetic is what lands here. The mapping keeps
BSD9 Total Assets == BSD2 item 13 and BSD9 Shareholders' funds == BSD2 item 16
by construction (proved in tests/services/bog_forms/test_bsd9.py).

Basis (Guide §1): consolidation only on BSD7B/BSD9. The platform holds the
parent's books, so every balance-sheet line carries the PARENT'S solo BSD2
figure and the sheet note says the bank adds subsidiaries' balances and
eliminations before filing. **Data-gap closure (2026-08-16):** the
``subsidiaries`` reference dataset (the subsidiary register + book, one row
per subsidiary per reporting date — schema
``app/domain/ingestion/reference_schemas/subsidiaries.py``, spec
docs/data_engine/datasets/subsidiaries.md) now feeds the two consolidation-
only blocks: "10. Minority interests" is ``refs.sum`` of the group's own
``minority_interest_ghs`` workings over fully consolidated subsidiaries, split
Domestic / Foreign by the subsidiary's ``functional_currency`` (Guide §2, via
``currency_field``); the Annexure lists, per subsidiary, the inter-company
RECEIVABLE (rows 11–19, amount due FROM the subsidiary) and PAYABLE (rows
20–28, amount due TO it) with the register's reporting date, the bank's own
type text and the subsidiary name (``refs.field`` ranked by amount, one
:class:`LineSpec` per column because each column reads a different field).
Blank (``input_required``) until the register is ingested; a bank with fewer
subsidiaries than slots leaves the remaining rows blank.
"""

from __future__ import annotations

from ..spec import LineSpec
from ._common import INPUT_REQUIRED, RowSource, grid_lines, leaf_lines

KIND = "subsidiaries"

_PARENT = (
    "parent (solo) figure from BSD2 — subsidiaries' balances and inter-company eliminations "
    "are not on the platform (no subsidiary book)"
)


def bsd2(*rows: int, note: str = "") -> RowSource:
    label = "BSD2 row " + "+".join(str(r) for r in rows)
    notes = f"{label}; {note}; {_PARENT}" if note else f"{label}; {_PARENT}"
    return RowSource("bsd9.bsd2_lines", {"rows": list(rows)}, notes=notes)


#: BSD9 row → BSD2 rows (sheet ``BSD2``; Domestic → B, Foreign → C).
_ROWS: dict[int, RowSource] = {
    # ---- FOREIGN ASSETS 1–5 = BSD2 A.1–5 -------------------------------------
    8: bsd2(7),
    9: bsd2(8),
    10: bsd2(9),
    11: bsd2(10),
    12: bsd2(11),
    # ---- DOMESTIC ASSETS 6–13 -------------------------------------------------
    14: bsd2(14, 15, note="cash on hand + claims on Bank of Ghana (BSD2 6(a)+(b))"),
    15: bsd2(
        21,
        30,
        33,
        note="claims on other depository institutions + other financial institutions + "
        "cheques for clearing drawn on banks (BSD2 6(c)+(d)+(e))",
    ),
    16: bsd2(34, note="bills / short-term investments (BSD2 item 7)"),
    18: bsd2(68, note="loans, overdrafts and other advances — gross sub-total (BSD2 item 8)"),
    19: bsd2(
        69,
        71,
        note="total debt provision + revaluation gains on non-performing loans (both BSD2 "
        "item-8 deductions from gross; BSD9 has no separate line for the latter)",
    ),
    20: bsd2(70, note="interest in suspense (BSD2 item 8)"),
    21: bsd2(72, note="securities other than shares — long-term investments (BSD2 item 9)"),
    22: bsd2(102, note="shares and other equities, net of impairment (BSD2 item 10)"),
    23: bsd2(113, note="other assets (BSD2 item 11)"),
    24: bsd2(114, note="property, plant & equipment net of depreciation (BSD2 item 12)"),
    # ---- SHAREHOLDERS' FUNDS AND LIABILITIES 8–23 -----------------------------
    27: bsd2(128, note="paid-up capital (BSD2 item 14)"),
    28: bsd2(129, note="reserves (BSD2 item 15)"),
    30: RowSource(
        "refs.sum",
        {
            "kind": KIND,
            "value_field": "minority_interest_ghs",
            "filters": {"consolidation_method": "full"},
            "currency_field": "functional_currency",
        },
        notes="Σ minority_interest_ghs (the group's own consolidation workings — non-controlling "
        "share of each FULLY consolidated subsidiary's equity) from the subsidiaries register at "
        "the latest reporting date on/before the period end; Domestic = subsidiaries whose "
        "functional currency is the bank's base currency, Foreign = any other (Guide §2); blank "
        "until the register is ingested",
    ),
    31: bsd2(136, note="other amounts allowed as capital (BSD2 item 17)"),
    32: bsd2(142, 168, note="long-term borrowings, foreign (BSD2 19) + domestic (BSD2 21)"),
    33: bsd2(178, note="cheques for clearing (BSD2 item 22)"),
    34: bsd2(138, 184, note="short-term borrowings, foreign (BSD2 18) + domestic (BSD2 23)"),
    35: bsd2(196, note="deposits of financial institutions (BSD2 item 24)"),
    36: bsd2(226, note="deposits of non-financial institutions, public and govt (BSD2 item 25)"),
    37: bsd2(
        146,
        note="deposits of non-residents (BSD2 item 20) — the only deposits BSD2 "
        "carries outside items 24–26",
    ),
    38: bsd2(259, note="special deposits (BSD2 item 26)"),
    39: bsd2(274, note="margins against contingent liabilities (BSD2 item 27)"),
    40: bsd2(277, note="bonds issued (BSD2 item 28)"),
    41: bsd2(278, note="other liabilities (BSD2 item 29)"),
    43: bsd2(282, note="contingent liabilities (BSD2 item 33)"),
    44: bsd2(283, note="managed funds, contra (BSD2 item 34)"),
}

# ---------------------------------------------------------------------------
# Annexure — Details of inter-company transactions (rows 11–28)
# ---------------------------------------------------------------------------

#: rows 11–19: amounts due FROM subsidiaries (receivable); rows 20–28: due TO them (payable)
ANNEX_RECEIVABLE_ROWS: range = range(11, 20)
ANNEX_PAYABLE_ROWS: range = range(20, 29)
ANNEX_COLUMNS: dict[str, str] = {"date": "A", "type": "B", "subsidiary": "C", "amount": "D"}

#: Annexure column → (register field for the receivable block, for the payable block, numeric)
_ANNEX_FIELDS: dict[str, tuple[str, str, bool]] = {
    "date": ("reporting_date", "reporting_date", False),
    "type": ("intercompany_receivable_type", "intercompany_payable_type", False),
    "subsidiary": ("name", "name", False),
    "amount": ("intercompany_receivable_ghs", "intercompany_payable_ghs", True),
}


def _annex_source(column: str, block: str, rank: int) -> RowSource:
    receivable = block == "receivable"
    field = _ANNEX_FIELDS[column][0 if receivable else 1]
    numeric = _ANNEX_FIELDS[column][2]
    order_by = "intercompany_receivable_ghs" if receivable else "intercompany_payable_ghs"
    what = (
        "due FROM the subsidiary (receivable)" if receivable else "due TO the subsidiary (payable)"
    )
    return RowSource(
        "refs.field",
        {
            "kind": KIND,
            "order_by": order_by,
            "desc": True,
            "index": rank,
            "field": field,
            "numeric": numeric,
        },
        notes=(
            f"inter-company balance {what}, subsidiary ranked {rank + 1} by that balance in the "
            f"subsidiaries register (latest reporting date on/before the period end): {field}; "
            "blank until the register is ingested or when the bank has fewer subsidiaries "
            "than slots (type text is the bank's own — blank if the register omits it)"
        ),
    )


def _annex_block(block: str, rows: range) -> tuple[LineSpec, ...]:
    lines: list[LineSpec] = []
    first = rows[0]
    for column, letter in ANNEX_COLUMNS.items():
        lines.extend(
            grid_lines(
                "BSD9",
                "Annexure",
                rows=rows,
                value_columns={column: letter},
                row_sources={row: _annex_source(column, block, row - first) for row in rows},
                code_prefix=f"BSD9.ANNEX.{block}.{column}",
                default=INPUT_REQUIRED,
            )
        )
    return tuple(lines)


LINES = {
    "BSD9": leaf_lines(
        "BSD9",
        "BSD9",
        value_columns={"domestic": "B", "foreign": "C"},
        row_sources=_ROWS,
        code_prefix="BSD9",
        default=INPUT_REQUIRED,
    ),
    "Annexure": (
        *_annex_block("receivable", ANNEX_RECEIVABLE_ROWS),
        *_annex_block("payable", ANNEX_PAYABLE_ROWS),
    ),
}
