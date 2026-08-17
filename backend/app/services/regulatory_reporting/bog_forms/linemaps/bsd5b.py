"""BSD5B — Consolidated Capital Adequacy Return (group, quarterly).

Official workbook ``FORM BSD5B REVISED.xls``: sheet ``CAR FORMAT-GROUP`` (item
number column B, amount column D; 94 captured inputs + 4 official data cells
the template leaves BLANK — D8 Paid-up Ordinary Share Capital, D24 Undisclosed
Reserves, D71 50% of NOP, D72 3-year average gross income — bound through
``grid_lines``; 14 BoG formulas incl. the group ratio D74 ``=D30/D73%`` and the
10% test D75) and the empty placeholder ``Sheet2`` (no cells). Line/cell map:
docs/bog_returns/bsd5b_line_map.md.

Basis (Guide §1 + template title): CONSOLIDATED. The platform holds no
subsidiary book, so every line that also exists on the solo return links to
the computed BSD5A cell (``form.cell`` — BSD5A is the declared dependency and
is computed first): the group figure equals the solo figure until subsidiary
consolidation data exists, and the note on every such line says so. Lines that
exist only on consolidation (Minority Interests; Minority Interests in Tier 2
Preferred Shares) read the ``subsidiaries`` reference dataset (data-gap
closure 2026-08-16 — ``refs.sum`` of the group's own minority-interest
workings over fully consolidated subsidiaries; blank until the register is
ingested). Lines the group form splits more
finely than the solo form (Capitalised / Latent Revaluation Reserves,
Cumulative Preference Shares) bind their own capital_component names directly.

Template quirks reproduced, never corrected (formula cells are BoG's): rows
29–33 of the asset side are formulas that COPY the Tier 1 deductions (D46=D14,
D47=D16, D48=D17, D49=D19) and D50 "80% of claims on Discount Houses" ``=D20``
(Net Tier 1 Capital); rows 50–51 (50% of NOP, 3-yr average gross income) have
no data cell in the template and D73 ``=D55+D65-D67-D68`` does not add them —
the values are still emitted at D71/D72 for completeness.
"""

from __future__ import annotations

from ._common import INPUT_REQUIRED, RowSource, grid_lines, leaf_lines
from .bsd5a import (
    CAPITALISED_REVALUATION,
    CUMULATIVE_PREFERENCE,
    HYBRID,
    LATENT_REVALUATION,
    REVALUATION_FIXED,
    SHEET_CAR,
    capital,
    capital_deduction,
    template_constants,
)

FORM = "BSD5B"
SHEET_GROUP = "CAR FORMAT-GROUP"
_SOLO = "solo figure from BSD5A (the platform holds no subsidiary book — add the "
_SOLO_TAIL = " of consolidated subsidiaries when a group book exists)"


def solo(ref: str, what: str, extra: str = "") -> RowSource:
    """Link to the computed BSD5A CAR FORMAT cell; the group figure equals the
    solo figure until subsidiary consolidation data exists."""
    return RowSource(
        "form.cell",
        {"form": "BSD5A", "sheet": SHEET_CAR, "ref": ref},
        notes=f"{_SOLO}{what}{_SOLO_TAIL}{extra}",
    )


#: Data-gap closure (2026-08-16): the two consolidation-only lines read the
#: ``subsidiaries`` reference dataset (schema
#: ``app/domain/ingestion/reference_schemas/subsidiaries.py``, spec
#: docs/data_engine/datasets/subsidiaries.md) — the group's own minority-
#: interest workings per fully consolidated subsidiary, summed. The solo-linked
#: lines stay parent-solo: adding subsidiaries' assets / capital to a
#: ``form.cell`` figure needs a combining resolver (framework ask in the doc).
SUBSIDIARIES = "subsidiaries"


def minority(field: str, what: str) -> RowSource:
    return RowSource(
        "refs.sum",
        {
            "kind": SUBSIDIARIES,
            "value_field": field,
            "filters": {"consolidation_method": "full"},
        },
        notes=(
            f"Σ {field} over fully consolidated subsidiaries in the subsidiaries register "
            f"(latest reporting date on/before the period end): {what}; blank until the "
            "register is ingested"
        ),
    )


_ROWS: dict[int, RowSource] = {
    # ---- Tier 1 (Guide: Composition of Capital 1.i–iii) ---------------------
    8: solo("E7", "paid-up ordinary share capital"),
    9: solo("E8", "disclosed reserves"),
    10: minority(
        "minority_interest_ghs",
        "minority interests (Guide 1.iii) — the group's own consolidation workings: the "
        "non-controlling share of each FULLY consolidated subsidiary's equity",
    ),
    11: solo("E9", "permanent non-cumulative preference shares"),
    # ---- Less: Tier 1 deductions ---------------------------------------------
    14: solo("E12", "goodwill/intangibles"),
    15: solo("E13", "losses not provided for"),
    16: solo(
        "E14",
        "investments in subsidiaries",
        " — on consolidation the investment in a CONSOLIDATED subsidiary is eliminated; only "
        "unconsolidated holdings remain deducted",
    ),
    17: solo("E15", "investments in the capital of other banks and financial institutions"),
    18: capital_deduction(
        *CAPITALISED_REVALUATION,
        notes="revaluation reserves capitalised into share capital — deducted from Tier 1 "
        "here and added back in Tier 2 (row 13)",
    ),
    19: solo("E16", "connected lending of a long-term nature"),
    # ---- Tier 2 (Guide: Composition of Capital 2.i–v) -----------------------
    21: capital(
        *CAPITALISED_REVALUATION,
        notes="the capitalised revaluation reserves deducted at row 10, admitted in Tier 2",
    ),
    22: capital(*REVALUATION_FIXED, notes="fixed-asset (premises) revaluation reserves"),
    23: capital(
        *LATENT_REVALUATION,
        notes="latent revaluation of long-term equity securities carried at historical cost",
    ),
    24: solo("E20", "undisclosed reserves (current-year profit/loss)"),
    25: capital(*CUMULATIVE_PREFERENCE, notes="cumulative preference shares (Guide 2.v)"),
    26: minority(
        "minority_interest_tier2_pref_ghs",
        "minority interests in Tier 2 preferred shares (Guide 2) — the non-controlling share "
        "of each FULLY consolidated subsidiary's qualifying preferred shares, per the group's "
        "own workings (nil for most groups)",
    ),
    27: capital(
        *HYBRID, notes="hybrid debt/equity instruments (cumulative preference shares are on row 17)"
    ),
    28: solo("E22", "subordinated term debt"),
    # ---- Adjusted total assets ----------------------------------------------
    32: solo("E27", "total assets"),
    34: solo("E29", "cedi cash on hand"),
    35: solo("E30", "forex cash on hand"),
    37: solo("E32", "cedi clearing account balance with BoG"),
    38: solo("E33", "forex account balance with BoG"),
    39: solo("E34", "funds under swaps with BoG"),
    40: solo("E35", "BoG bills and bonds"),
    43: solo("E39", "treasury securities"),
    44: solo("E40", "government stocks"),
    45: solo("E41", "80% of cheques drawn on other banks"),
    51: solo("E47", "80% of claims on other banks"),
    52: solo("E51", "50% of residential mortgage loans"),
    53: solo("E52", "50% of export financing loans"),
    54: solo("E50", "80% of loans guaranteed by multilateral banks"),
    # ---- Off-balance-sheet items -------------------------------------------
    58: solo("E55", "commercial letters of credit outstanding"),
    59: solo("E56", "guarantees / indemnities"),
    60: solo("E57", "acceptances"),
    61: solo("E58", "endorsements"),
    62: solo("E59", "revolving underwriting facilities"),
    63: solo("E60", "note issuance facilities"),
    64: solo("E61", "standby letters of credit to other banks"),
    67: solo("E63", "50% of class-1 risk-weighted off-balance-sheet items"),
    68: solo("E64", "80% of class-2 risk-weighted off-balance-sheet items"),
    # ---- Add-ons (no data cell in the template; D73 does not add them) -----
    71: solo("E67", "50% of NOP", " — informational: BoG's D73 formula omits this row"),
    72: solo(
        "E68",
        "100% of the 3-year average annual gross income",
        " — informational: BoG's D73 formula omits this row",
    ),
}

#: official data cells the template leaves blank (no ``0`` placeholder), so
#: ``layout.input_cells`` cannot see them — bound explicitly
_BLANK_ROWS: tuple[int, ...] = (8, 24, 71, 72)

LINES = {
    SHEET_GROUP: (
        *leaf_lines(
            FORM,
            SHEET_GROUP,
            value_columns={"amount": "D"},
            row_sources=_ROWS,
            code_prefix="BSD5B.GROUP",
            default=INPUT_REQUIRED,
        ),
        *grid_lines(
            FORM,
            SHEET_GROUP,
            rows=_BLANK_ROWS,
            value_columns={"amount": "D"},
            row_sources=_ROWS,
            code_prefix="BSD5B.GROUP.BLANK",
            default=INPUT_REQUIRED,
        ),
        *leaf_lines(
            FORM,
            SHEET_GROUP,
            value_columns={"no": "B"},
            row_sources=template_constants(
                FORM, SHEET_GROUP, "B", notes="official item number as printed in the template"
            ),
            code_prefix="BSD5B.GROUP.NO",
        ),
        # E15: an unlabeled cell carrying ``0`` in the official workbook (column E
        # has no header) — reproduced verbatim so the export matches the template.
        *leaf_lines(
            FORM,
            SHEET_GROUP,
            value_columns={"stray": "E"},
            row_sources={
                15: RowSource(
                    "constant",
                    {"value": 0},
                    notes="template artefact — unlabeled cell holding 0 in the official "
                    "workbook (column E has no header); not a BoG line, reproduced as 0",
                    unscaled=True,
                )
            },
            code_prefix="BSD5B.GROUP.STRAY",
        ),
    ),
}
