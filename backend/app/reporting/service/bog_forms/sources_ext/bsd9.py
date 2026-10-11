"""BSD9 resolver — Consolidated Balance Sheet lines from the computed BSD2.

BSD9 is a condensed BSD2: every BSD9 line is one BSD2 line or a roll-up of
several, in the same Domestic / Foreign columns. ``bsd9.bsd2_lines`` selects the
BSD2 cells for the column being filled (``domestic`` → column ``B``, ``foreign``
→ ``C``, ``total`` → ``D``) across the declared BSD2 rows and sums them — the
BSD2 form is computed first (BSD9 ``depends_on`` BSD2), so BoG's own BSD2
arithmetic (sub-totals, TOTAL column) is what lands here, never a second
implementation of it. Row-level values that BSD2 could not fill
(``input_required``) are blank; a BSD9 line whose every source cell is blank
stays blank (input_required) instead of reading as a zero.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from ..sources import ResolveContext, resolver

_COLUMN_LETTER = {"domestic": "B", "foreign": "C", "total": "D"}


@resolver("bsd9.bsd2_lines")
def _bsd2_lines(rc: ResolveContext, params: dict[str, Any]) -> Decimal | None:
    """Σ over ``rows`` of the computed BSD2 ``BSD2`` sheet cell in this column.

    params: ``rows`` (BSD2 row numbers), ``sheet`` (default ``"BSD2"``),
    ``sign`` (default 1). None when BSD2 has not been computed or every
    referenced cell is blank.
    """
    dep = rc.dependencies.get("BSD2")
    if dep is None:
        return None
    letter = _COLUMN_LETTER.get(rc.column, "D")
    sheet = str(params.get("sheet", "BSD2"))
    total = Decimal(0)
    found = False
    for row in params.get("rows", ()):
        raw = dep.get((sheet, f"{letter}{int(row)}"))
        if raw is None or isinstance(raw, str):
            continue
        total += Decimal(str(raw))
        found = True
    if not found:
        return None
    return total * Decimal(str(params.get("sign", 1)))
