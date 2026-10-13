"""BSD8 — Advances Subject to Adverse Classification.

Official layout: sheet ``BSD8`` (summary: items 1–14 × Current / Olem /
Substandard / Doubtful / Loss in columns C–G, Total H = template SUM; item 8
"Current balance (Gross)" and item 12 "Net current balance" are template
formulas; item 14 ``H22`` is BoG's external link ``[1]BSD2!D38+[1]BSD2!D39``)
and ``BSD8-Annexure`` (the 50 largest adversely classified advances, one
facility per row: Name of Customer … Comments; I = G+H, K = I+J and the TOTAL
row / % line are template formulas). Line/cell map:
docs/bog_returns/bsd8_line_map.md.

Every official input cell of both sheets is bound below. The classification
buckets come from ``bsd8.bucket`` (sources_ext/bsd8.py): the source's
``bog_classification`` attribute, else the IFRS 9 stage proxy (1 → Current,
2 → OLEM, 3 → non-performing — whose Substandard / Doubtful / Loss split the
stage cannot state, so those cells stay input_required until the source
classifies). The monthly MOVEMENT rows (items 2–7) are input_required — the
canonical model holds positions and snapshots, not loan-book flows — so item
8 equals the platform's opening balance until the bank supplies the
movements; the Guide's own arithmetic then closes the schedule.
"""

from __future__ import annotations

from dataclasses import replace

from ..layout import load_layout
from ..spec import LineSpec
from ._common import INPUT_REQUIRED, RowSource, grid_lines, leaf_lines

_FORM = "BSD8"
_SUMMARY = "BSD8"
_ANNEXURE = "BSD8-Annexure"

#: Column key → letter; the key doubles as the ``classification`` the resolver
#: reads from ``rc.column``.
_BUCKET_COLUMNS: dict[str, str] = {
    "current": "C",
    "olem": "D",
    "substandard": "E",
    "doubtful": "F",
    "loss": "G",
}

_CLASSIFICATION_NOTE = (
    "by classification: source `bog_classification` attribute, else IFRS 9 stage proxy "
    "(1→Current, 2→Olem, 3→non-performing; the Substandard/Doubtful/Loss split of a "
    "stage-3-only book is input_required)"
)


def _bucket(measure: str, notes: str, **params: object) -> RowSource:
    return RowSource("bsd8.bucket", {"measure": measure, **params}, notes=notes)


_SUMMARY_ROWS: dict[int, RowSource] = {
    # 1. Previous balance (Gross) — opening position as at the previous month-end
    7: _bucket(
        "balance",
        "gross cedi balance of LOAN positions as at the previous month-end (latest snapshot "
        f"on/before the day before the reporting period starts), {_CLASSIFICATION_NOTE}",
        as_of="previous",
    ),
    # 2–7: the month's movements — loan-book flows are not in the canonical model
    8: RowSource(
        None,
        notes="new advances made during the month — disbursement flow; the canonical model "
        "holds positions/snapshots, not loan-book movements. Bank must supply.",
    ),
    9: RowSource(
        None,
        notes="interest charged for the month by classification — interest/accruals "
        "sub-ledger required. Bank must supply.",
    ),
    10: RowSource(
        None,
        notes="revaluation gains/loss for the month on foreign-currency advances — "
        "bank must supply.",
    ),
    11: RowSource(
        None,
        notes="amounts recovered during the month — repayment/recovery flow; bank must supply.",
    ),
    12: RowSource(
        None,
        notes="amounts written off during the month — write-off register; bank must supply.",
    ),
    13: RowSource(
        None,
        notes="(+/-) net reclassification between buckets during the month — cannot be "
        "separated from items 2–6 without the movement schedule; bank must supply.",
    ),
    # 8 (b). Of which foreign currency (of item 8) — current FX-denominated balance
    15: _bucket(
        "balance",
        "cedi equivalent of foreign-currency LOAN positions as at the reporting date, "
        f"{_CLASSIFICATION_NOTE}",
        currency="FX",
    ),
    # 9. Revaluation gains (on NPL, cumulative)
    16: RowSource(
        None,
        notes="cumulative revaluation gains on non-performing FX advances — bank must "
        "supply (same gap as BSD2 line 8 'Revaluation gains on non-performing loans').",
    ),
    # 10. Interest in suspense (cumulative)
    17: _bucket(
        "interest_in_suspense",
        "Σ `interest_in_suspense_ghs` attribute of LOAN positions; input_required when the "
        f"source carries no such attribute, {_CLASSIFICATION_NOTE}",
    ),
    # 11. Allowable security (cash & near-cash)
    18: _bucket(
        "security",
        "Σ `crm_collateral_ghs` where `crm_collateral_class` ∈ CASH / SOVEREIGN_DEBT (Guide "
        "'Security': cash and government stocks); other readily-realisable classes need "
        "the bank's confirmation; input_required when the source carries no collateral "
        f"attributes, {_CLASSIFICATION_NOTE}",
        collateral_classes=["CASH", "SOVEREIGN_DEBT"],
    ),
    # 12 (b). Of which foreign currency (of item 12)
    20: RowSource(
        None,
        notes="foreign-currency share of the NET balance (item 12) — needs the FX split of "
        "items 9–11 (revaluation, suspense, security) which the source does not carry; "
        "bank must supply.",
    ),
    # 13. Provisions required (on 12)
    21: _bucket(
        "provision",
        "Σ `ecl_provision_ghs` (position-level allowance held) by classification; the "
        "Guide's minimum (1/10/25/50/100% of the net unsecured balance) is not "
        "recomputed here — bank confirms/overrides; input_required when the source "
        f"carries no provision attribute, {_CLASSIFICATION_NOTE}",
    ),
}


def _serial_numbers(sheet: str, *, code_prefix: str) -> tuple[LineSpec, ...]:
    """The template's printed item / serial numbers in column A were captured as
    numeric INPUT cells; reproduce the template's own literal (unscaled)."""
    layout = load_layout(_FORM).sheet(sheet)
    sources: dict[int, RowSource] = {
        cell.row: RowSource(
            "constant",
            {"value": cell.value},
            notes="template item number (printed literal)",
            unscaled=True,
        )
        for cell in layout.input_cells
        if cell.ref.startswith("A") and cell.ref[1:].isdigit()
    }
    return leaf_lines(
        _FORM,
        sheet,
        value_columns={"no": "A"},
        row_sources=sources,
        code_prefix=code_prefix,
    )


# --- BSD8-Annexure: 50 detail rows (template rows 5–54), one facility each ---
_ANNEXURE_FIRST_ROW = 5
_ANNEXURE_ROWS = range(_ANNEXURE_FIRST_ROW, _ANNEXURE_FIRST_ROW + 50)
#: Column key (= the resolver's field) → official column letter (header row 4).
#: I ("Total Funded Credits") and K ("Total Exposure") are template formulas.
_ANNEXURE_COLUMNS: dict[str, str] = {
    "name": "B",
    "branch": "C",
    "sector": "D",
    "facility": "E",
    "expiry_date": "F",
    "capital": "G",
    "interest": "H",
    "obs": "J",
    "security_value": "L",
    "security_type": "M",
    "classification": "N",
    "provision": "O",
    "comments": "P",
}
_ANNEXURE_NOTE = (
    "50 largest adversely classified advances (Olem and worse), one facility per row, "
    "largest cedi balance first: name ← counterparty; branch ← `branch_id`; sector ← "
    "`sector`; facility ← product; expiry ← contractual maturity; capital ← balance "
    "(cedi); interest ← input_required (accruals sub-ledger); OBS ← Σ LC/guarantee + "
    "undrawn-commitment notional of the customer (shown on its first row); security ← "
    "`crm_collateral_ghs`/`crm_collateral_class`; classification ← bucket (stage-3-only "
    "→ input_required: bank states Substandard/Doubtful/Loss); provision ← "
    "`ecl_provision_ghs`; comments ← bank. Rows past the end of the list are blank."
)


def _annexure_lines() -> tuple[LineSpec, ...]:
    lines = grid_lines(
        _FORM,
        _ANNEXURE,
        rows=list(_ANNEXURE_ROWS),
        value_columns=_ANNEXURE_COLUMNS,
        row_sources={
            row: RowSource(
                "bsd8.annexure",
                {"rank": row - _ANNEXURE_FIRST_ROW + 1},
                notes=_ANNEXURE_NOTE,
            )
            for row in _ANNEXURE_ROWS
        },
        code_prefix="BSD8.Annexure",
        default=INPUT_REQUIRED,
    )

    # the detail rows carry no label cell — name them by rank for the notes sheet
    def rank_of(line: LineSpec) -> int:
        row = int(line.cells["name"][1:])
        return row - _ANNEXURE_FIRST_ROW + 1

    return tuple(replace(line, label=f"Detail row {rank_of(line)}") for line in lines)


def _summary_lines() -> tuple[LineSpec, ...]:
    lines = leaf_lines(
        _FORM,
        _SUMMARY,
        value_columns=_BUCKET_COLUMNS,
        row_sources=_SUMMARY_ROWS,
        code_prefix="BSD8",
        default=INPUT_REQUIRED,
    )
    # rows 15 / 20 print their item number ("8 (b)") in column A and the
    # description in column B — carry both into the line label
    layout = load_layout(_FORM).sheet(_SUMMARY)

    def full_label(line: LineSpec) -> str:
        row = int(line.cells["current"][1:])
        description = layout.by_ref.get(f"B{row}")
        text = str(description.value).strip() if description is not None else ""
        return f"{line.label} {text}".strip() if text and text != line.label else line.label

    return tuple(replace(line, label=full_label(line)) for line in lines)


LINES = {
    _SUMMARY: (
        *_summary_lines(),
        *_serial_numbers(_SUMMARY, code_prefix="BSD8.No"),
    ),
    _ANNEXURE: (
        *_annexure_lines(),
        *_serial_numbers(_ANNEXURE, code_prefix="BSD8.Annexure.No"),
    ),
}
