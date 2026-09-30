"""The contractual maturity ladder's horizons and bucket rule (pure).

Five LMTD horizons (Phase 2 item 2; FRM 16-17), lifted verbatim from
``app.services.regulatory_liquidity`` (P0-8) so the BI plane can place a
position on the same ladder the per-currency return derives. Undated
demand-natured items (cash; current/call/savings deposits) sit in the shortest
bucket; other undated items in the longest.
"""

from __future__ import annotations

from datetime import date

#: Upper bound in days of each ladder bucket; ``None`` marks the open-ended last one.
LADDER_HORIZON_DAYS: tuple[int | None, ...] = (30, 91, 182, 365, None)


def ladder_bucket_index(maturity: date | None, as_of: date, *, on_demand: bool) -> int:
    """Index into ``LADDER_HORIZON_DAYS`` for a position's contractual maturity.

    ``on_demand`` decides where an UNDATED position goes: the shortest bucket
    when it is demand-natured, otherwise the longest. A dated position takes the
    first bucket whose horizon is at or beyond its remaining days; a maturity
    already in the past is a negative day count and lands in the first bucket.
    """
    if maturity is None:
        return 0 if on_demand else len(LADDER_HORIZON_DAYS) - 1
    days = (maturity - as_of).days
    for index, upper in enumerate(LADDER_HORIZON_DAYS):
        if upper is None or days <= upper:
            return index
    return len(LADDER_HORIZON_DAYS) - 1
