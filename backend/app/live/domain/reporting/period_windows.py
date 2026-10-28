"""Trend windows over a bank's reporting periods (pure, no I/O).

``bank_reporting_periods`` is one row per ingested as-of date, not the
regulator's calendar (AGENTS.md): a monthly feeder has ~12 rows a year, a daily
feeder ~250. A trend that takes "the last N rows" is therefore a different
horizon per tenant — 13 month-ends for one bank, 13 business days for another —
which is what the five module dashboards did with ``_TREND_MAX_POINTS = 13``.

The window is defined in calendar months instead (BI decision D-014, the
spec's own ``bi_dim_date`` semantics): the last period WITH DATA in each of the
``months`` calendar months preceding the latest period's month, plus the latest
period itself. "Month-end" is therefore the last date that has a period in the
month, never the calendar's last day — a bank that books its month on the last
business day has no calendar month-ends at all. Months without a period are
simply absent (nothing is interpolated or zero-filled), and the latest period is
always the final point whether or not it closes a month.

Every dashboard's ``_build_trend`` and ``_prefetch_dashboard_batch`` select
through this one function, as does the query-shape oracle, so the three cannot
disagree about which periods a trend covers.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from typing import Any, Protocol

#: Calendar months of history a dashboard trend shows before the latest period.
#: With one period per month that is the 12 month-ends of a trailing year plus
#: the latest point — the 13-point sparkline the dashboards always drew.
TREND_MONTHS = 12


class HasPeriodEnd(Protocol):
    """Anything anchored on a business date — a ``BankReportingPeriod`` or a stub.

    A Protocol because ``app/domain`` must not import ``app/models``. The member
    is typed ``Any`` for the reason ``domain/authority/provenance.py::RunLike``
    records: the ORM declares ``period_end`` as a ``Mapped[date]`` descriptor,
    and a static checker matches a Protocol against the DECLARED type, so a
    ``date`` member would reject every real call site. The value IS a ``date``
    at runtime; ``_month_ordinal`` below is the typed boundary.
    """

    @property
    def period_end(self) -> Any: ...


def _month_ordinal(value: date) -> int:
    return value.year * 12 + value.month - 1


def trailing_month_end_window[P: HasPeriodEnd](
    periods: Iterable[P], *, months: int = TREND_MONTHS
) -> list[P]:
    """Select the trend window from a bank's periods, ascending by ``period_end``.

    Returns the last period in each of the ``months`` calendar months before the
    latest period's month, followed by the latest period — at most ``months + 1``
    items. Input order does not matter; the result is ascending. Empty in, empty
    out.
    """
    if months < 0:
        raise ValueError("months must be zero or positive")
    latest: P | None = None
    last_in_month: dict[int, P] = {}
    for period in periods:
        if latest is None or period.period_end > latest.period_end:
            latest = period
        ordinal = _month_ordinal(period.period_end)
        incumbent = last_in_month.get(ordinal)
        if incumbent is None or period.period_end > incumbent.period_end:
            last_in_month[ordinal] = period
    if latest is None:
        return []
    latest_ordinal = _month_ordinal(latest.period_end)
    first_ordinal = latest_ordinal - months
    selected = [
        last_in_month[ordinal]
        for ordinal in sorted(last_in_month)
        if first_ordinal <= ordinal < latest_ordinal
    ]
    selected.append(latest)
    return selected
