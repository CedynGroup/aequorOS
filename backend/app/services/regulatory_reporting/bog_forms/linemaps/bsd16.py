"""BSD16 — Monthly Report on Operations of ATM (monthly, ¢'Million).

Official layout: sheet ``MONTHLY ATM OPERATIONS`` — 50 station/branch rows
(7–56) × Station / Branch (B) · No. of Cards Issued (C) · Minimum withdrawal
made ¢ (D) · Maximum withdrawal made ¢ (E); F = ``=D+E`` per row and F57
``=SUM(F7:F56)`` are BoG's formulas. Column A holds the template's row ordinals
1–50 (captured as numeric input cells; kept as constants). ``Sheet2`` / ``Sheet3``
are empty placeholder tabs (no BoG lines). The data grid is BLANK in the
template, so B/C/D/E are bound explicitly with :func:`_common.grid_lines`.

**Data-gap closure (2026-08-16):** every data cell reads the bank's
``atm_operations`` reference dataset (docs/data_engine/datasets/
atm_operations.md — one row per terminal for ONE reporting month, uploaded or
pushed through the Data Engine with the month-end as ``as_of_date``; BSD16
takes the latest batch on/before the period end):

* station row ``7 + i`` ↔ the ``i``-th terminal row of the register in file
  order (``refs.field index=i`` — no ``order_by``, so the bank controls the
  order by the order of its file): ``station`` (text) → B, ``cards_issued``
  → C (a count, unscaled), ``min_withdrawal_ghs`` → D and
  ``max_withdrawal_ghs`` → E (cedis; the sheet's ¢'Million divisor applies at
  export). A register with fewer than 50 terminals leaves the tail rows blank
  (``input_required`` — not an error, the official grid is simply larger than
  the estate); a register with more than 50 shows its first 50 (the official
  grid has 50 rows) and the TOTAL row still sums the whole register.
* TOTAL row 57: D57 / E57 carry no template formula — they are bound to
  ``refs.sum`` over the whole register (Σ ``min_withdrawal_ghs`` /
  Σ ``max_withdrawal_ghs``: the bank's own column totals, not a BoG rule);
  ``F57 = SUM(F7:F56)`` stays BoG's formula.

Before the register is ingested every resolver returns ``None`` and each cell
stays ``input_required`` naming the dataset, exactly as before.
"""

from __future__ import annotations

from ._common import RowSource, grid_lines

_FORM = "BSD16"
_SHEET = "MONTHLY ATM OPERATIONS"
_ROWS = range(7, 57)
_FIRST_ROW = 7
KIND = "atm_operations"

_ATM_DATASET = (
    "atm_operations register required (docs/data_engine/datasets/atm_operations.md — one row "
    "per terminal for the reporting month: station, cards_issued, min_withdrawal_ghs, "
    "max_withdrawal_ghs); blank when the register has fewer terminals than this ordinal"
)
_TOTAL_NOTE = (
    "official TOTAL row: F57 is BoG's SUM formula; D57/E57 carry no template formula — bound to "
    "the register's own column totals (Σ min_withdrawal_ghs / Σ max_withdrawal_ghs over every "
    "terminal row)"
)


def _serial(n: int) -> RowSource:
    return RowSource(
        "constant", {"value": n}, notes="template row ordinal (official value kept)", unscaled=True
    )


def _terminal(row: int, field_name: str, *, numeric: bool, unscaled: bool = False) -> RowSource:
    """The ``field_name`` of the (row − 7)-th terminal row of the register (file order)."""
    return RowSource(
        "refs.field",
        {"kind": KIND, "index": row - _FIRST_ROW, "field": field_name, "numeric": numeric},
        notes=_ATM_DATASET,
        unscaled=unscaled,
    )


def _column_total(field_name: str) -> RowSource:
    return RowSource("refs.sum", {"kind": KIND, "value_field": field_name}, notes=_TOTAL_NOTE)


LINES = {
    _SHEET: (
        *grid_lines(
            _FORM,
            _SHEET,
            rows=_ROWS,
            value_columns={"serial": "A"},
            row_sources={row: _serial(row - 6) for row in _ROWS},
            code_prefix="BSD16.serial",
        ),
        *grid_lines(
            _FORM,
            _SHEET,
            rows=_ROWS,
            value_columns={"station": "B"},
            row_sources={row: _terminal(row, "station", numeric=False) for row in _ROWS},
            code_prefix="BSD16.station",
        ),
        *grid_lines(
            _FORM,
            _SHEET,
            rows=_ROWS,
            value_columns={"cards_issued": "C"},
            row_sources={
                row: _terminal(row, "cards_issued", numeric=True, unscaled=True) for row in _ROWS
            },
            code_prefix="BSD16.cards",
        ),
        *grid_lines(
            _FORM,
            _SHEET,
            rows=_ROWS,
            value_columns={"min_withdrawal": "D"},
            row_sources={row: _terminal(row, "min_withdrawal_ghs", numeric=True) for row in _ROWS},
            code_prefix="BSD16.min",
        ),
        *grid_lines(
            _FORM,
            _SHEET,
            rows=_ROWS,
            value_columns={"max_withdrawal": "E"},
            row_sources={row: _terminal(row, "max_withdrawal_ghs", numeric=True) for row in _ROWS},
            code_prefix="BSD16.max",
        ),
        *grid_lines(
            _FORM,
            _SHEET,
            rows=(57,),
            value_columns={"min_withdrawal": "D"},
            row_sources={57: _column_total("min_withdrawal_ghs")},
            code_prefix="BSD16.total.min",
        ),
        *grid_lines(
            _FORM,
            _SHEET,
            rows=(57,),
            value_columns={"max_withdrawal": "E"},
            row_sources={57: _column_total("max_withdrawal_ghs")},
            code_prefix="BSD16.total.max",
        ),
    ),
}
