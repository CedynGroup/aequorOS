"""BSD15A — Rates of Commission and Other Charges of Banks (Domestic).

Official workbook ``FORM BSD15A REVISED.xls`` (weekly, 9 days), two sheets:

* ``Domestic charges of banks`` — a 22-item tariff schedule (COT, ledger
  fees, maintenance fees, drafts, returned/stopped cheques, transfers,
  lending fees, statements, clearing, standing orders, salary/pension
  processing, closures, safe custody, cheque books, ATM, Sika card …). Item
  numbers 1–22 sit in column A (the template's only captured input cells —
  kept as constants), the item text in column B, and the charge is entered in
  the first value column ``C`` (the template's value columns C.. carry a
  numeric format but no header text; C is bound, see the doc's "Framework
  asks").
* ``Range of pdts i.r.o sav & cur `` — "RANGE OF PRODUCTS OF BANKS IN RESPECT
  OF SAVINGS AND CURRENT ACCOUNTS (Amounts in Cedis)": for each product
  S1–S7 / C1–C3 / foreign accounts the initial deposit, minimum operating
  balance, maintenance fee, penal charges, transaction fees …; item text in
  column A, amount in column ``B``.

**Data-gap closure (2026-08-16):** every tariff cell reads the bank's
``tariff_schedule`` reference dataset (docs/data_engine/datasets/
tariff_schedule.md — uploaded or pushed through the Data Engine, one row per
official tariff cell) through ``refs.field``: the row is addressed by
``(form, sheet, row_key)`` and the cell carries the row's ``charge_value`` —
text on the two "text" sheets, a cedi amount on the Range sheet. Until the
register is ingested the resolver returns ``None`` and every cell stays
``input_required`` ("tariff register required"), so the structure exports
verbatim and the Completion notes list every line the bank must supply.
Text-only rows are labels (exported as-is).

Which rows take a value: every labelled row of the schedule EXCEPT a heading
that introduces sub-lines — a numbered item heading (item number in column A
/ ``"n. "`` prefix) or a colon-terminated group label — when it is followed
by sub-lines. When in doubt a row is bound (a spare blank input cell costs
nothing; a dropped official line would). See :func:`tariff_rows`.

``row_key`` (:func:`tariff_row_keys`) is stable and bank-facing:
``"<item>.<n>"`` — the official item the value row sits under (the number in
column A / the ``"n. "`` label prefix, or the product code in parentheses on
the Range sheet: ``S1``…``S7``, ``C1``…``C3``, ``FX`` for the foreign-account
block) and the 1-based ordinal of the value row within that item — e.g.
BSD15A DOMESTIC ``1.1`` = COT minimum, RANGE ``S1.1`` = Savings S1 initial
deposit, BSD15B INTL ``17.1`` = account closure. The dataset doc lists every
key with its official label (generated from this module — regenerate, never
hand-edit).

Line/cell map: docs/bog_returns/bsd15a_line_map.md.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from ..layout import load_layout
from ._common import RowSource, grid_lines, leaf_lines

FORM = "BSD15A"
DOMESTIC = "Domestic charges of banks"
RANGE = "Range of pdts i.r.o sav & cur "

#: bank-facing sheet codes used in the ``tariff_schedule`` dataset
DOMESTIC_CODE = "DOMESTIC"
RANGE_CODE = "RANGE"
TARIFF_KIND = "tariff_schedule"

TARIFF_INPUT = RowSource(
    None,
    notes=(
        "tariff register required — no fee/charge register exists on the platform; bank must supply"
    ),
)

_NUMBERED = re.compile(r"^\s*(\d+)\.\s")
_PRODUCT_CODE = re.compile(r"\(([A-Z]\d+)\)")


def tariff_rows(  # noqa: PLR0913 - one keyword per structural dimension
    form: str,
    sheet: str,
    *,
    label_column: str,
    first_row: int,
    last_row: int,
    numbered_column: str | None = None,
) -> tuple[int, ...]:
    """The rows of a tariff schedule that take a value.

    Every labelled row in ``[first_row, last_row]`` except a heading with
    sub-lines: a numbered item heading (a captured item number in
    ``numbered_column`` or a ``"n. "`` label prefix) or a colon-terminated
    label, when the next labelled row is not itself a numbered heading.
    Childless headings (e.g. BSD15B "17. ACCOUNT CLOSURE") take the value.
    """
    layout = load_layout(form).sheet(sheet)
    labels: dict[int, str] = {}
    for cell in layout.cells:
        ref = cell.ref
        if (
            cell.kind == "label"
            and ref.startswith(label_column)
            and ref[len(label_column) :].isdigit()
            and first_row <= cell.row <= last_row
        ):
            labels[cell.row] = str(cell.value)
    numbered: set[int] = set()
    if numbered_column is not None:
        numbered = {
            c.row
            for c in layout.input_cells
            if c.ref.startswith(numbered_column) and c.ref[len(numbered_column) :].isdigit()
        }
    ordered = sorted(labels)
    rows: list[int] = []
    for index, row in enumerate(ordered):
        text = labels[row]
        is_heading = row in numbered or bool(_NUMBERED.match(text)) or text.rstrip().endswith(":")
        has_children = False
        if index + 1 < len(ordered):
            nxt = ordered[index + 1]
            has_children = nxt not in numbered and not _NUMBERED.match(labels[nxt])
        if is_heading and has_children:
            continue
        rows.append(row)
    return tuple(rows)


def tariff_row_keys(  # noqa: PLR0913 - one keyword per structural dimension
    form: str,
    sheet: str,
    *,
    rows: Sequence[int],
    label_column: str,
    first_row: int,
    last_row: int,
    numbered_column: str | None = None,
    item_tokens: Mapping[int, str] | None = None,
) -> dict[int, tuple[str, str]]:
    """``{official row: (row_key, official label)}`` for the value rows of a
    tariff schedule.

    ``row_key = "<item>.<n>"``: ``item`` is the token of the numbered heading
    the row sits under — the captured item number in ``numbered_column``, else
    the ``"n. "`` prefix of the heading label, else the product code in
    parentheses (``(S1)`` → ``S1``; parenthesised codes win over the number so
    the Range sheet's savings/current products read ``S1``…``C3``);
    ``item_tokens`` overrides a heading's token by row (the Range sheet's
    un-coded, re-numbered "FOREIGN ACCOUNTS" heading → ``FX``). ``n`` is the
    1-based ordinal of the value row within its item; rows before the first
    heading sit under item ``0``. Keys are unique per sheet by construction.
    """
    layout = load_layout(form).sheet(sheet)
    labels: dict[int, str] = {}
    for cell in layout.cells:
        ref = cell.ref
        if (
            cell.kind == "label"
            and ref.startswith(label_column)
            and ref[len(label_column) :].isdigit()
            and first_row <= cell.row <= last_row
        ):
            labels[cell.row] = str(cell.value)
    numbered: dict[int, str] = {}
    if numbered_column is not None:
        numbered = {
            c.row: str(c.value)
            for c in layout.input_cells
            if c.ref.startswith(numbered_column) and c.ref[len(numbered_column) :].isdigit()
        }
    value_rows = set(rows)
    keys: dict[int, tuple[str, str]] = {}
    item = "0"
    ordinal = 0
    for row in sorted(labels):
        text = labels[row]
        token: str | None = None
        if item_tokens and row in item_tokens:
            token = item_tokens[row]
        elif row in numbered:
            token = numbered[row].split(".")[0]
        elif (code := _PRODUCT_CODE.search(text)) is not None:
            token = code.group(1)
        elif (num := _NUMBERED.match(text)) is not None:
            token = num.group(1)
        if token is not None:
            # a new item starts here; a childless heading is its own first value row
            item, ordinal = token, 0
        if row in value_rows:
            ordinal += 1
            keys[row] = (f"{item}.{ordinal}", text.strip())
    return keys


def tariff_sources(
    form: str, sheet_code: str, keys: Mapping[int, tuple[str, str]], *, numeric: bool
) -> dict[int, RowSource]:
    """Bind every keyed row to its ``tariff_schedule`` row's ``charge_value``."""
    return {
        row: RowSource(
            "refs.field",
            {
                "kind": TARIFF_KIND,
                "filters": {"form": form, "sheet": sheet_code, "row_key": key},
                "field": "charge_value",
                "numeric": numeric,
            },
            notes=(
                f"tariff register required — tariff_schedule row form={form} sheet={sheet_code} "
                f"row_key={key} ({label!r}); charge_value is the cell "
                f"({'cedi amount' if numeric else 'text as published'})"
            ),
            unscaled=True,
        )
        for row, (key, label) in keys.items()
    }


def _numbered_item_constants(rows: Sequence[int]) -> dict[int, RowSource]:
    """The item numbers 1..n shipped in the template's column A."""
    return {
        row: RowSource("constant", {"value": index}, notes="template item number", unscaled=True)
        for index, row in enumerate(rows, start=1)
    }


_DOMESTIC_ITEM_ROWS = tuple(
    c.row for c in load_layout(FORM).sheet(DOMESTIC).input_cells if c.ref.startswith("A")
)
DOMESTIC_ROWS = tariff_rows(
    FORM, DOMESTIC, label_column="B", first_row=9, last_row=231, numbered_column="A"
)
RANGE_ROWS = tariff_rows(FORM, RANGE, label_column="A", first_row=12, last_row=120)
#: The Range sheet's "10.      FOREIGN ACCOUNTS" heading re-uses item number 10
#: (C3 is also "10.") and carries no product code — keyed ``FX``.
_RANGE_ITEM_TOKENS = {109: "FX"}

DOMESTIC_KEYS = tariff_row_keys(
    FORM,
    DOMESTIC,
    rows=DOMESTIC_ROWS,
    label_column="B",
    first_row=9,
    last_row=231,
    numbered_column="A",
)
RANGE_KEYS = tariff_row_keys(
    FORM,
    RANGE,
    rows=RANGE_ROWS,
    label_column="A",
    first_row=12,
    last_row=120,
    item_tokens=_RANGE_ITEM_TOKENS,
)

LINES = {
    DOMESTIC: (
        *leaf_lines(
            FORM,
            DOMESTIC,
            value_columns={"item_no": "A"},
            row_sources=_numbered_item_constants(_DOMESTIC_ITEM_ROWS),
            code_prefix="BSD15A.DOMESTIC.ITEM",
        ),
        *grid_lines(
            FORM,
            DOMESTIC,
            rows=DOMESTIC_ROWS,
            value_columns={"charge": "C"},
            row_sources=tariff_sources(FORM, DOMESTIC_CODE, DOMESTIC_KEYS, numeric=False),
            code_prefix="BSD15A.DOMESTIC",
            default=TARIFF_INPUT,
            label_column="B",
        ),
    ),
    RANGE: grid_lines(
        FORM,
        RANGE,
        rows=RANGE_ROWS,
        value_columns={"amount": "B"},
        row_sources=tariff_sources(FORM, RANGE_CODE, RANGE_KEYS, numeric=True),
        code_prefix="BSD15A.RANGE",
        default=TARIFF_INPUT,
        label_column="A",
    ),
}
