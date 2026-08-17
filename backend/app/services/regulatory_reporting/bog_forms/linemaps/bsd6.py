"""BSD6 — Maturity Analysis of Assets and Liabilities (BSD6A cedis / BSD6B foreign).

Official layout (``FORM BSD6 REVISED.xls``): two sheets, ``BSD6A`` (60 leaf rows)
and ``BSD6B`` (50 leaf rows); every leaf row carries TEN input cells —
``B`` FROM BSD2 · ``C`` Total · ``D`` Overdue · ``E`` Less than 1 month ·
``F`` 1 month–<3 months · ``G`` 3–<6 months · ``H`` 6 months–<1 year ·
``I`` 1–<3 years · ``J`` 3–<5 years · ``K`` 5 years and over — and every
section total / sub-total / Total Assets / Shareholders' Funds & Liabilities /
Assets − Liabilities row is a template formula (150 + 140), which the engine
evaluates. Line/cell map: docs/bog_returns/bsd6_line_map.md.

The Guide's rule for this form is "FROM BSD2": BSD6A analyses, by residual
maturity, the CEDI assets and liabilities of BSD2 (its Domestic column) and
BSD6B the FOREIGN-CURRENCY ones (its Foreign column); the totals must agree
with BSD2. This map therefore never re-declares a filter: every BSD6 row names
the BSD2 line(s) it aggregates and the ``bsd6.bucket`` resolver reuses BSD2's
own line map (``line_maps_for("BSD2")``) — the same resolver and parameters —
for the Total column, splitting it by residual maturity for the band columns
and reading the BSD2 cell itself for FROM BSD2. A BSD2 correction flows into
BSD6 automatically; a BSD2 line that is ``input_required`` / CoA-mapping stays
so here (the official cells are still emitted).

The workbook has NO "Annex 1" sheet: the Guide's note that unmatured
spot/forward positions are reported only in BSD6B Annex 1 has no cell in the
official file, so there is nothing to bind (recorded in the doc).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..layout import load_layout
from . import line_maps_for
from ._common import BANK_COA_MAPPING, INPUT_REQUIRED, RowSource, leaf_lines

#: Column key → template column (BSD6A and BSD6B share the header row).
_COLUMNS: dict[str, str] = {
    "from_bsd2": "B",
    "total": "C",
    "overdue": "D",
    "lt_1m": "E",
    "1m_lt_3m": "F",
    "3m_lt_6m": "G",
    "6m_lt_1y": "H",
    "1y_lt_3y": "I",
    "3y_lt_5y": "J",
    "5y_plus": "K",
}

#: Guide (BSD6 notes) placement of fact-sourced BSD2 lines that carry no
#: maturity: (band, band when the amount is negative).
_FIXED_BANDS: dict[int, tuple[str, str | None]] = {
    14: ("overdue", None),  # cash on hand → Overdue
    16: ("overdue", None),  # sight balances with BoG → Overdue
    17: ("5y_plus", "overdue"),  # special deposits (reserves) → 5y+; negative → Overdue
    69: ("overdue", None),  # provisions for bad debts → Overdue
    128: ("5y_plus", "overdue"),  # paid-up capital (perpetual) → with Reserves
    130: ("5y_plus", "overdue"),  # reserves → 5 years and above; negative → Overdue
    131: ("5y_plus", "overdue"),
    132: ("5y_plus", "overdue"),
    133: ("5y_plus", "overdue"),
    134: ("5y_plus", "overdue"),
}
_BAND_LABEL = {
    "overdue": "Overdue",
    "lt_1m": "less than 1 month",
    "5y_plus": "5 years and over",
}

#: BSD2 leaves that BoG's own roll-up SUBTRACTS from the line a BSD6 row reads
#: (row 123 "Less Depreciation": item 12 = SUM(115:121) − 123) — their sourced
#: component enters this row's Total with the sign flipped so Total == FROM BSD2.
_DEDUCTION_LEAVES: frozenset[int] = frozenset({123})

_BSD2_LINES = {int(line.cells["domestic"][1:]): line for line in line_maps_for("BSD2")["BSD2"]}
_BSD2_LAYOUT = load_layout("BSD2").sheet("BSD2")


@dataclass(frozen=True)
class _Row:
    """One BSD6 leaf row: the BSD2 line it reads (``anchor``, the FROM BSD2
    cell) and the BSD2 leaf rows whose sources it aggregates."""

    side: str  # asset | liability
    anchor: int | None
    leaves: tuple[int, ...]
    note: str = ""


def _bsd2_label(row: int) -> str:
    return _BSD2_LAYOUT.label_for_row(row) or f"row {row}"


def _row_source(spec: _Row, *, bsd2_column: str, letter: str) -> RowSource:
    components: list[dict[str, Any]] = []
    unsourced: list[int] = []
    coa_only = True
    for row in spec.leaves:
        line = _BSD2_LINES[row]
        if line.source is None:
            unsourced.append(row)
            coa_only = coa_only and line.notes == BANK_COA_MAPPING.notes
            continue
        component: dict[str, Any] = {"source": line.source, "params": dict(line.params)}
        if row in _DEDUCTION_LEAVES:
            component["params"]["sign"] = -1 * float(component["params"].get("sign", 1))
        if line.source == "facts.sum" and row in _FIXED_BANDS:
            band, negative = _FIXED_BANDS[row]
            component["bucket"] = band
            component["negative_bucket"] = negative
        components.append(component)
    if not components:
        # Every BSD2 leaf behind this row is unsourced → same status here.
        if unsourced and coa_only:
            note = f"{BANK_COA_MAPPING.notes} (BSD2 rows {_span(unsourced)})"
            return RowSource(None, notes=f"{note}; {spec.note}" if spec.note else note)
        detail = "; ".join(f"BSD2 row {r}: {_BSD2_LINES[r].notes}" for r in unsourced)
        return RowSource(None, notes=" — ".join(part for part in (spec.note, detail) if part))
    refs = (
        [f"{letter}{spec.anchor}"]
        if spec.anchor is not None
        else [f"{letter}{r}" for r in spec.leaves]
    )
    notes = [f"FROM BSD2 {refs[0] if len(refs) == 1 else '+'.join(refs)}"]
    if spec.anchor is not None:
        notes[0] += f" ({_bsd2_label(spec.anchor)[:60]})"
    notes.extend(_placement_notes(spec))
    if unsourced:
        notes.append(f"BSD2 rows {_span(unsourced)} unsourced (see BSD2 map)")
    if spec.note:
        notes.append(spec.note)
    return RowSource(
        "bsd6.bucket",
        {
            "side": spec.side,
            "bsd2_column": bsd2_column,
            "bsd2_refs": refs,
            "components": components,
        },
        notes="; ".join(notes),
    )


def _placement_notes(spec: _Row) -> list[str]:
    """Completion-sheet notes on how the row's non-position components are banded."""
    out: list[str] = []
    placed = sorted(
        {
            _BAND_LABEL.get(_FIXED_BANDS[r][0], _FIXED_BANDS[r][0])
            for r in spec.leaves
            if r in _FIXED_BANDS and _BSD2_LINES[r].source == "facts.sum"
        }
    )
    if placed:
        out.append("Guide band for fact-sourced lines: " + ", ".join(placed))
    if any(_BSD2_LINES[r].source == "facts.sum" and r not in _FIXED_BANDS for r in spec.leaves):
        out.append("no Guide band for this fact-sourced line — bank allocates the bands")
    kinds = {
        str(_BSD2_LINES[r].params.get("kind"))
        for r in spec.leaves
        if _BSD2_LINES[r].source == "refs.sum"
    }
    if "interest_accruals" in kinds:
        out.append(
            "accrued interest from the accruals sub-ledger (interest_accruals) — no Guide "
            "maturity band; counted in Total, bands left to the bank when non-zero"
        )
    if "capital_expenditure" in kinds:
        out.append(
            "fixed assets from the capital_expenditure register (cost + WIP − accumulated "
            "depreciation, so Total == FROM BSD2) — no Guide maturity band; bands left to the "
            "bank when non-zero"
        )
    return out


def _span(rows: list[int]) -> str:
    rows = sorted(rows)
    if len(rows) > 2 and rows[-1] - rows[0] == len(rows) - 1:  # noqa: PLR2004
        return f"{rows[0]}–{rows[-1]}"
    return ", ".join(str(r) for r in rows)


_A = "asset"
_L = "liability"

# --- rows shared by BSD6A and BSD6B (same official labels, same BSD2 lines) ---
_SHARED: dict[int, _Row] = {
    # 2. Bills (BSD2 7. Bills — short-term investments)
    13: _Row(_A, 35, (36, 37, 38, 39)),
    14: _Row(_A, 40, (41, 42, 43, 44, 45, 46)),
    15: _Row(_A, 47, (48, 49, 50)),
    16: _Row(_A, 51, (51,)),
    17: _Row(_A, 52, (52,)),
    18: _Row(_A, 53, (54, 55, 56)),
    19: _Row(_A, 57, (58, 59)),
    # 3. Loans, overdrafts and other advances (BSD2 8.)
    21: _Row(_A, 61, (61,)),
    22: _Row(_A, 62, (62,)),
    23: _Row(_A, 65, (65,), "BSD2 (c) Public enterprises — (ii) Others"),
    24: _Row(_A, 66, (66,)),
    25: _Row(_A, 64, (64,), "BSD2 (c) Public enterprises — (i) Cocoa Syndicated Loan"),
    26: _Row(_A, 67, (67,)),
    27: _Row(
        _A,
        None,
        (),
        "no BSD2 counterpart (BSD2 8. has no 'Others' borrower class) — loans outside "
        "the classes above; bank to classify (blank keeps Sub-total = BSD2 Sub-total)",
    ),
    29: _Row(_A, 69, (69,)),
    30: _Row(_A, 70, (70,), "Guide: interest in suspense → Overdue column"),
    31: _Row(_A, 71, (71,)),
    # 4.–7.
    32: _Row(
        _A,
        72,
        (74, 75, 76, 77, 78, 80, 81, 84, 85, 86, 87, 88, 89, 91, 92, 94, 95, 97, 98, 100, 101),
        "Guide: investments with issuer repayment options at the latest repayment date",
    ),
    33: _Row(_A, 102, (104, 105, 106, 107, 108, 109, 110, 112), "equities: undated → 5y+"),
    34: _Row(_A, 113, (113,)),
    35: _Row(_A, 114, (115, 116, 117, 118, 119, 120, 121, 123)),
    # 9.–11. capital
    41: _Row(_L, 128, (128,), "perpetual: placed with Reserves (Guide note) in 5 years and over"),
    43: _Row(_L, 130, (130,)),
    44: _Row(_L, 131, (131,)),
    45: _Row(_L, 132, (132,)),
    46: _Row(_L, 133, (133,)),
    47: _Row(_L, 134, (134,)),
    49: _Row(_L, 136, (136,)),
}

_BSD6A: dict[int, _Row] = {
    **_SHARED,
    # 1. Cash and balances due from other financial institutions (BSD2 6.)
    7: _Row(_A, 14, (14,)),
    8: _Row(_A, 15, (16, 17, 18, 19, 20)),
    9: _Row(_A, 21, (22, 23, 25, 26, 27, 28, 29)),
    10: _Row(_A, 30, (31, 32)),
    11: _Row(_A, None, (20, 29, 32), "accrued interest on BoG balances / placements / OFIs"),
    # 12. Long-term borrowing (BSD2 21.)
    51: _Row(_L, 175, (175,)),
    52: _Row(_L, 169, (169,)),
    53: _Row(_L, 170, (171, 172, 173)),
    54: _Row(_L, 174, (174,)),
    55: _Row(_L, 176, (176,)),
    56: _Row(_L, 177, (177,)),
    # 13. Cheques for clearing (BSD2 22.)
    57: _Row(_L, 178, (180, 181, 182, 183)),
    # 14. Short-term borrowing (BSD2 23.)
    59: _Row(_L, 185, (186, 187, 188)),
    60: _Row(_L, 189, (190, 191)),
    61: _Row(_L, 192, (192,)),
    62: _Row(_L, 193, (193,)),
    63: _Row(_L, 194, (194,)),
    64: _Row(_L, 195, (195,)),
    # 15. Deposits of financial institutions (BSD2 24.)
    66: _Row(_L, 197, (198, 200, 201, 202, 203, 204)),
    67: _Row(_L, 205, (206, 208, 209, 210, 211)),
    68: _Row(_L, 212, (213, 215, 216, 217, 218)),
    69: _Row(_L, 219, (220, 222, 223, 224, 225)),
    # 16. Deposits of non-financial institutions, public and govt. (BSD2 25.)
    71: _Row(_L, 227, tuple(range(228, 235)), "Guide 15&16: demand/savings by withdrawal pattern"),
    72: _Row(_L, 235, tuple(range(236, 243)), "Guide 15&16: demand/savings by withdrawal pattern"),
    73: _Row(_L, 243, tuple(range(244, 251))),
    74: _Row(_L, 251, tuple(range(252, 259))),
    # 17.–20.
    75: _Row(_L, 259, (*range(261, 267), *range(268, 274))),
    76: _Row(_L, 274, (275, 276)),
    77: _Row(_L, 277, (277,)),
    78: _Row(_L, 278, (278,)),
    # 23.–24. memoranda
    81: _Row(_L, 282, (282,), "contingent liabilities at notional (BSD2 33.)"),
    82: _Row(_L, 283, (283,)),
}

_BSD6B: dict[int, _Row] = {
    **_SHARED,
    # 1. Cash and balances due from other financial institutions
    7: _Row(_A, 7, (7,), "BSD2 A.1 Foreign currency notes and coins (Guide: cash → Overdue)"),
    8: _Row(_A, 14, (14,), "BSD2 6(a) Cash on hand, foreign column"),
    9: _Row(_A, 8, (8,), "BSD2 A.2 Correspondent accounts in non-resident financial institutions"),
    10: _Row(_A, 21, (22, 23, 25, 26, 27, 28, 29)),
    11: _Row(_A, 30, (31, 32)),
    # 12. Long-term borrowing (BSD2 C.19 — foreign liabilities)
    51: _Row(_L, 143, (143,)),
    52: _Row(_L, 144, (144,)),
    53: _Row(_L, 145, (145,)),
    # 13. Short-term borrowing (BSD2 C.18)
    55: _Row(_L, 139, (139,)),
    56: _Row(_L, 140, (140,)),
    57: _Row(_L, 141, (141,)),
    # 14. Deposits of non-residents (BSD2 C.20)
    59: _Row(_L, 147, (148, 149, 150, 151), "Guide 15&16: demand/savings by withdrawal pattern"),
    60: _Row(_L, 152, (153, 154, 155, 156), "Guide 15&16: demand/savings by withdrawal pattern"),
    61: _Row(_L, 157, (158, 159, 160, 161)),
    62: _Row(_L, 162, (163, 164, 165, 166)),
    63: _Row(_L, None, (151, 156, 161, 166), "accrued interest on non-resident deposits"),
    # 15.–18.
    64: _Row(_L, 259, (*range(261, 267), *range(268, 274))),
    65: _Row(_L, 274, (275, 276)),
    66: _Row(_L, 277, (277,)),
    67: _Row(_L, 278, (278,)),
    # 21.–22. memoranda
    70: _Row(_L, 282, (282,), "contingent liabilities at notional (BSD2 33.)"),
    71: _Row(_L, 283, (283,)),
}


def _sources(rows: dict[int, _Row], *, bsd2_column: str, letter: str) -> dict[int, RowSource]:
    return {
        row: _row_source(spec, bsd2_column=bsd2_column, letter=letter) for row, spec in rows.items()
    }


LINES = {
    "BSD6A": leaf_lines(
        "BSD6",
        "BSD6A",
        value_columns=_COLUMNS,
        row_sources=_sources(_BSD6A, bsd2_column="domestic", letter="B"),
        code_prefix="BSD6A",
        default=INPUT_REQUIRED,
    ),
    "BSD6B": leaf_lines(
        "BSD6",
        "BSD6B",
        value_columns=_COLUMNS,
        row_sources=_sources(_BSD6B, bsd2_column="foreign", letter="C"),
        code_prefix="BSD6B",
        default=INPUT_REQUIRED,
    ),
}
