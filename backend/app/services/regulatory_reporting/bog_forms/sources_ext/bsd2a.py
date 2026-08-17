"""BSD2A resolvers — sums and ratios over the computed BSD2 cells.

BSD2A analyses the FOREIGN column of BSD2 (Guide BSD2A ¶1). Where one BSD2A
category heading spans SEVERAL BSD2 lines (e.g. "(d) Securities" = BSD2 7 bills
+ 9 long-term securities + 10 shares) a single ``form.cell`` cannot express the
category total, and the Guide's "%age of exposure to net worth" column
(¶5(vii): "the percentage each exposure bears to the net worth of the reporting
bank", net worth = shareholders' funds as reported on BSD2, ¶5(vi)) is a ratio
of two BSD2 cells. Both are aggregation/selection of already-computed
dependency cells — nothing here computes a BoG figure by a new rule.

Resolvers::

    bsd2a.form_cells_sum        {form, sheet, refs: [..]}
        Σ of the named computed cells of a dependency form (None when the
        dependency has not been computed or every ref is blank).
    bsd2a.form_cells_ratio_pct  {form, sheet, numerator: [..], denominator: [..]}
        100 × Σ numerator / Σ denominator; None when either side is blank or the
        denominator is zero (the cell stays input_required rather than showing a
        misleading 0 %). Bind with ``unscaled=True`` — it is a percentage.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from ..sources import ResolveContext, resolver


def _sum_refs(rc: ResolveContext, form: str, sheet: str, refs: list[str]) -> Decimal | None:
    dep = rc.dependencies.get(form)
    if dep is None:
        return None
    values = [dep.get((sheet, ref)) for ref in refs]
    numeric = [Decimal(str(v)) for v in values if isinstance(v, (int, float, Decimal))]
    if not numeric:
        return None
    return sum(numeric, Decimal(0))


@resolver("bsd2a.form_cells_sum")
def _form_cells_sum(rc: ResolveContext, params: dict[str, Any]) -> Decimal | None:
    return _sum_refs(rc, params["form"], params["sheet"], list(params["refs"]))


@resolver("bsd2a.form_cells_ratio_pct")
def _form_cells_ratio_pct(rc: ResolveContext, params: dict[str, Any]) -> Decimal | None:
    form, sheet = params["form"], params["sheet"]
    numerator = _sum_refs(rc, form, sheet, list(params["numerator"]))
    denominator = _sum_refs(rc, form, sheet, list(params["denominator"]))
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator * Decimal(100)
