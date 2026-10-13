"""BSD3B — Large Exposures: Advances and Deposits of Subsidiaries (GROUP).

Official workbook ``FORM BSD3B - REVISED GROUP.xls``: the SAME three sheets as
BSD3A (same cells, same template formulas), on the group basis the catalogue
sets — Guide BSD3 item 7: "BSD3-GROUP: the required information should be for
each subsidiary". The map is therefore BSD3A's map (:func:`bsd3a.build_lines`)
in ``subsidiary`` scope: every roster / count cell is ``input_required`` and
only the template's own row numbers (Sheet 1 ``A27:A30``) resolve.

Data-gap closure (2026-08-16, documented decision): the ``subsidiaries``
reference dataset (docs/data_engine/datasets/subsidiaries.md) is the
subsidiary REGISTER — identity, ownership, the subsidiary's balance sheet and
inter-company balances — not a subsidiary POSITION book, and the rosters
(each subsidiary's twenty largest depositors, ten largest monetary-sector and
fifty largest non-monetary-sector exposures, with maturities, currency splits
and security) are position-level facts the register cannot honestly carry, so
no roster cell is re-pointed. The register does fix WHICH subsidiaries the
return covers; the notes below say so and name the design that closes the
rest: a ``subsidiary_id``-keyed subsidiary position book (or a
``subsidiary_exposures`` roster dataset: one row per (reporting_date,
subsidiary_id, roster, rank) with the sheet's fields) read through
``refs.field`` with ``filters={"subsidiary_id": …}``, ``order_by`` amount,
``index`` = rank − 1 — plus per-subsidiary emission by the framework (the
catalogue says one workbook PER subsidiary; the generator emits one workbook,
so only the first subsidiary would be emitted). See
docs/bog_returns/bsd3b_line_map.md ("Data-gap closure" + "Framework asks").
"""

from __future__ import annotations

from dataclasses import replace

from ..spec import LineSpec
from .bsd3a import build_lines

#: The pre-register wording of :mod:`bsd3a`'s subsidiary-scope note (matched by
#: content so BSD3A's module keeps no BSD3B-specific export).
_PRE_REGISTER_NOTE = "no subsidiary register or subsidiary position book"

#: What replaces the pre-register note on every subsidiary-scope cell.
SUBSIDIARY_ROSTER_NOTE = (
    "BSD3-GROUP is required for EACH subsidiary (Guide BSD3 item 7); the subsidiaries register "
    "(reference dataset `subsidiaries`) names the subsidiaries but is not a subsidiary position "
    "book, so the subsidiary's roster cannot be derived — bank must supply per subsidiary until a "
    "subsidiary_id-keyed subsidiary exposure book is ingested (BSD3A's bsd3.rank map then applies "
    "per subsidiary unchanged)"
)


def _with_register_notes(
    lines: dict[str, tuple[LineSpec, ...]],
) -> dict[str, tuple[LineSpec, ...]]:
    return {
        sheet: tuple(
            replace(line, notes=SUBSIDIARY_ROSTER_NOTE)
            if line.source is None and _PRE_REGISTER_NOTE in line.notes
            else line
            for line in sheet_lines
        )
        for sheet, sheet_lines in lines.items()
    }


LINES = _with_register_notes(build_lines("BSD3B", "subsidiary"))
