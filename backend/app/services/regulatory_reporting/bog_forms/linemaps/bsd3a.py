"""BSD3A — Large Exposures: Advances and Deposits (solo).

Official workbook ``FORM BSD3A REVISED.xls`` — three ranked rosters whose data
cells are BLANK in the template (no ``0`` placeholder), so they are bound with
:func:`_common.grid_lines`; every row total (``G = E + F`` / ``F = D + E`` /
``G = D + E + F``), the Sheet-1 total ``G26 = SUM(G6:G25)``, ``G28`` and the
percentage ``G29`` are the template's own formulas and are never bound.

Sheet ``BSD3-Sheet-1`` (twenty largest depositors), rows 6–25 = ranks 1–20:
``B`` customer · ``C`` type of account · ``D`` final maturity date · ``E``
foreign-currency component (cedi equivalent) · ``F`` cedi component · ``H``
remarks; row 27 accrued interest (``E27``/``F27``); row 30 ``G30`` total number
of depositors; ``A27:A30`` are the template's own row numbers 22–25 (numeric,
hence captured as inputs — bound to their constant so the official numbering
survives the values-only export).

Sheet ``BSD3-Sheet-2`` (ten largest monetary-sector exposures), rows 6–15:
``B`` customer · ``C`` final maturity · ``D`` foreign · ``E`` cedi · ``G`` of
which on balance sheet · ``H`` value of security · ``I`` remarks.

Sheet ``BSD3-Sheet-3`` (fifty largest non-monetary-sector exposures), rows
5–54: ``B`` customer · ``C`` type of exposure · ``D`` drawn · ``E`` undrawn ·
``F`` other contingent · ``H`` value of security · ``I`` type of security · ``J``
remarks.

Sources: ``bsd3.rank`` / ``bsd3.count`` (``sources_ext/bsd3.py`` — the LE/LMT
canonical slice, connected-counterparty grouping). Value/type of security
(Guide: the ORIGINAL valuation at grant; ``crm_collateral_ghs`` is the CRM-
eligible value, a different figure) and remarks are ``input_required``.
:func:`build_lines` is shared with BSD3B (group basis, per subsidiary).
Line/cell map: docs/bog_returns/bsd3a_line_map.md.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from ..spec import LineSpec
from ._common import RowSource, grid_lines, leaf_lines

SHEET_1 = "BSD3-Sheet-1"
SHEET_2 = "BSD3-Sheet-2"
SHEET_3 = "BSD3-Sheet-3"

#: rank rows per sheet (rank = row − first_row + 1)
SHEET_1_ROWS: range = range(6, 26)  # 20 largest depositors
SHEET_2_ROWS: range = range(6, 16)  # 10 largest monetary-sector exposures
SHEET_3_ROWS: range = range(5, 55)  # 50 largest non-monetary-sector exposures

#: official data columns → resolver field (the column key IS the field name)
SHEET_1_COLUMNS: dict[str, str] = {
    "name": "B",
    "account_type": "C",
    "maturity": "D",
    "foreign": "E",
    "cedi": "F",
}
SHEET_2_COLUMNS: dict[str, str] = {
    "name": "B",
    "maturity": "C",
    "foreign": "D",
    "cedi": "E",
    "on_balance": "G",
}
SHEET_3_COLUMNS: dict[str, str] = {
    "name": "B",
    "exposure_type": "C",
    "drawn": "D",
    "undrawn": "E",
    "contingent": "F",
}
SHEET_1_ACCRUED_ROW = 27
SHEET_1_COUNT_ROW = 30
SHEET_1_NUMBER_ROWS = (27, 28, 29, 30)  # A27:A30 hold the template's 22–25

_SECURITY_NOTE = (
    "value / type of security — the Guide asks for the ORIGINAL valuation at grant and the "
    "asset charged (fixed charge, floating charge, guarantee); the canonical model holds only "
    "the CRM-eligible collateral value (crm_collateral_ghs), a different figure — bank must supply"
)
_REMARKS_NOTE = "remarks — free text; bank elaborates on items not clear from the figures"
_ACCRUED_NOTE = (
    "accrued interest on the twenty largest depositors (Guide item 22) — accruals sub-ledger "
    "required (same gap as BSD2's accrued-interest lines)"
)
_SUBSIDIARY_NOTE = (
    "BSD3-GROUP is required for EACH subsidiary (Guide BSD3 item 7); the platform holds no "
    "subsidiary register or subsidiary position book, so the subsidiary's roster cannot be "
    "derived — bank must supply per subsidiary (BSD3A's bsd3.rank map applies unchanged once a "
    "subsidiary book is ingested)"
)

Scope = Literal["solo", "subsidiary"]


def _rank_source(kind: str, rank: int, scope: Scope) -> RowSource:
    if scope == "subsidiary":
        return RowSource(None, notes=_SUBSIDIARY_NOTE)
    return RowSource(
        "bsd3.rank",
        {"kind": kind, "rank": rank},
        notes=(
            f"rank {rank} {kind.replace('_', ' ')} — canonical positions aggregated per "
            "counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer "
            f"than {rank} ranked counterparties"
        ),
    )


def _rank_sources(kind: str, rows: Sequence[int], scope: Scope) -> dict[int, RowSource]:
    first = rows[0]
    return {row: _rank_source(kind, row - first + 1, scope) for row in rows}


def build_lines(form: str, scope: Scope) -> dict[str, tuple[LineSpec, ...]]:
    """The BSD3 line map for ``form`` (BSD3A solo / BSD3B per-subsidiary)."""
    prefix = form
    numbering: dict[int, RowSource] = {
        row: RowSource(
            "constant",
            {"value": 22 + index},
            notes="template row number (a numeric label the extractor captured as an input) — "
            "constant keeps the official numbering in the values-only export",
            unscaled=True,
        )
        for index, row in enumerate(SHEET_1_NUMBER_ROWS)
    }
    count_source = (
        RowSource(None, notes=_SUBSIDIARY_NOTE)
        if scope == "subsidiary"
        else RowSource(
            "bsd3.count",
            {"kind": "depositor"},
            notes="number of distinct counterparties holding DEPOSIT positions (all depositors, "
            "not only the twenty listed) — a count, unscaled",
            unscaled=True,
        )
    )
    sheet_1 = (
        *grid_lines(
            form,
            SHEET_1,
            rows=SHEET_1_ROWS,
            value_columns=SHEET_1_COLUMNS,
            row_sources=_rank_sources("depositor", SHEET_1_ROWS, scope),
            code_prefix=f"{prefix}.S1",
        ),
        *grid_lines(
            form,
            SHEET_1,
            rows=SHEET_1_ROWS,
            value_columns={"remarks": "H"},
            row_sources={},
            code_prefix=f"{prefix}.S1.remarks",
            default=RowSource(None, notes=_REMARKS_NOTE),
        ),
        *grid_lines(
            form,
            SHEET_1,
            rows=[SHEET_1_ACCRUED_ROW],
            value_columns={"foreign": "E", "cedi": "F"},
            row_sources={SHEET_1_ACCRUED_ROW: RowSource(None, notes=_ACCRUED_NOTE)},
            code_prefix=f"{prefix}.S1",
        ),
        *grid_lines(
            form,
            SHEET_1,
            rows=[SHEET_1_COUNT_ROW],
            value_columns={"count": "G"},
            row_sources={SHEET_1_COUNT_ROW: count_source},
            code_prefix=f"{prefix}.S1",
        ),
        *leaf_lines(
            form,
            SHEET_1,
            value_columns={"number": "A"},
            row_sources=numbering,
            code_prefix=f"{prefix}.S1.number",
        ),
    )
    sheet_2 = (
        *grid_lines(
            form,
            SHEET_2,
            rows=SHEET_2_ROWS,
            value_columns=SHEET_2_COLUMNS,
            row_sources=_rank_sources("monetary_exposure", SHEET_2_ROWS, scope),
            code_prefix=f"{prefix}.S2",
        ),
        *grid_lines(
            form,
            SHEET_2,
            rows=SHEET_2_ROWS,
            value_columns={"security_value": "H"},
            row_sources={},
            code_prefix=f"{prefix}.S2.security",
            default=RowSource(None, notes=_SECURITY_NOTE),
        ),
        *grid_lines(
            form,
            SHEET_2,
            rows=SHEET_2_ROWS,
            value_columns={"remarks": "I"},
            row_sources={},
            code_prefix=f"{prefix}.S2.remarks",
            default=RowSource(None, notes=_REMARKS_NOTE),
        ),
    )
    sheet_3 = (
        *grid_lines(
            form,
            SHEET_3,
            rows=SHEET_3_ROWS,
            value_columns=SHEET_3_COLUMNS,
            row_sources=_rank_sources("non_monetary_exposure", SHEET_3_ROWS, scope),
            code_prefix=f"{prefix}.S3",
        ),
        *grid_lines(
            form,
            SHEET_3,
            rows=SHEET_3_ROWS,
            value_columns={"security_value": "H", "security_type": "I"},
            row_sources={},
            code_prefix=f"{prefix}.S3.security",
            default=RowSource(None, notes=_SECURITY_NOTE),
        ),
        *grid_lines(
            form,
            SHEET_3,
            rows=SHEET_3_ROWS,
            value_columns={"remarks": "J"},
            row_sources={},
            code_prefix=f"{prefix}.S3.remarks",
            default=RowSource(None, notes=_REMARKS_NOTE),
        ),
    )
    return {SHEET_1: sheet_1, SHEET_2: sheet_2, SHEET_3: sheet_3}


LINES = build_lines("BSD3A", "solo")
