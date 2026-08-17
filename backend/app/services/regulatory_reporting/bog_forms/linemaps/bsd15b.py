"""BSD15B — International Banking Charges (Charges — Foreign).

Official workbook ``FORM BSD15B REVISED.xls`` (weekly, 9 days), one sheet
``International Banking Charges``: a 21-section tariff schedule (letters of
credit — imports / exports, SGS facility, documentary bills for collection,
clean bills, commitment fees, guarantees/bonds, exchange control, inward /
outward transfers, forex drafts, travellers' cheques, cheque lodgements,
account closure, returned cheques, COT, forex account maintenance,
statements). Item text sits in column A; the charge is entered in the first
value column ``B`` (the template's value columns carry number formats but no
header text — see the doc's "Framework asks").

**Data-gap closure (2026-08-16):** every tariff cell reads the bank's
``tariff_schedule`` reference dataset (docs/data_engine/datasets/
tariff_schedule.md) through ``refs.field`` — row addressed by ``form=BSD15B``,
``sheet=INTL``, ``row_key`` (:func:`bsd15a.tariff_row_keys`: ``"<item>.<n>"``,
e.g. ``1.1`` = L/C imports establishment commission, ``17.1`` = account
closure) — and carries the row's ``charge_value`` text. Until the register is
ingested every cell stays ``input_required`` ("tariff register required") at
its official cell; the structure is exported verbatim and every line is listed
in the Completion notes. Row selection follows :func:`bsd15a.tariff_rows`
(every labelled row except a heading that introduces sub-lines).

Line/cell map: docs/bog_returns/bsd15b_line_map.md.
"""

from __future__ import annotations

from ._common import grid_lines
from .bsd15a import TARIFF_INPUT, tariff_row_keys, tariff_rows, tariff_sources

FORM = "BSD15B"
SHEET = "International Banking Charges"
#: bank-facing sheet code used in the ``tariff_schedule`` dataset
SHEET_CODE = "INTL"

ROWS = tariff_rows(FORM, SHEET, label_column="A", first_row=9, last_row=392)
KEYS = tariff_row_keys(FORM, SHEET, rows=ROWS, label_column="A", first_row=9, last_row=392)

LINES = {
    SHEET: grid_lines(
        FORM,
        SHEET,
        rows=ROWS,
        value_columns={"charge": "B"},
        row_sources=tariff_sources(FORM, SHEET_CODE, KEYS, numeric=False),
        code_prefix="BSD15B",
        default=TARIFF_INPUT,
        label_column="A",
    ),
}
