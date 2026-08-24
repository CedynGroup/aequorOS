"""The snapshot ladder — ``bank_reporting_periods`` and what it actually is.

A row here is **the key for one computed fact snapshot**, not a filing calendar.
It exists because ``bank_financial_facts`` is unique on
``(reporting_period_id, fact_group, category)``: the period row is how a derived
set of facts is addressed and superseded. It is created by the data path — the
ingestion pipeline and fact derivation — as a consequence of a book arriving
with an as-of date.

What it is NOT (founder review, 2026-08-23)
-------------------------------------------
It is not the list of dates the institution reports on. Those are the
regulator's, come from the return registry, and are enumerated by
``services/regulatory_reporting/anchors.py``. The Returns workspace used to
offer these ``period_end`` values as its "Reporting date" list, which made
BoG's calendar a function of the bank's ingestion cadence — see the anchors
module docstring for the measurement of what that cost.

The relationship runs one way and only one way:

    return definition ──▶ reporting date ──▶ snapshot lookup (exact, may miss)

so this module is deliberately unexported to the API surface.

The ``period_start`` convention
-------------------------------
``period_start`` is the first day of ``period_end``'s calendar month, and that
is a real convention rather than filler: it is the fiscal month-to-date window
that flow figures accumulate over. Three consumers depend on it —
``bog_forms/sources_ext/bsd7.py`` (year-to-date start, ``max(period_start,
fy_start)``), ``bog_forms/sources_ext/bsd8.py`` (opening balance, the day before
``period_start``) and ``implied_rating.py`` (which requires an annual span).
Balance figures in the snapshot are point-in-time as of ``period_end``; only
flows read ``period_start``. Do not collapse it to ``period_end``.
"""

from __future__ import annotations

from calendar import monthrange
from datetime import date

from app.models import BankReportingPeriod


def snapshot_label(as_of: date) -> str:
    """A label that identifies the snapshot unambiguously.

    Month-end snapshots keep the historical ``YYYY-MM`` form — that is what the
    120 month-end rows on the primary carry and what every monthly return's
    provenance shows. An intra-month snapshot (a Friday weekly close, a daily
    business-day book) gets the full ``YYYY-MM-DD``, because ``YYYY-MM`` would
    name two different snapshots in the same month identically: the table is
    unique on ``(bank_id, period_end)``, not on the label, so a bank ingesting
    weekly produced four rows a month all labelled the same.
    """
    if as_of.day == monthrange(as_of.year, as_of.month)[1]:
        return f"{as_of.year:04d}-{as_of.month:02d}"
    return as_of.isoformat()


def new_snapshot_period(
    *, organization_id: str, bank_id: str, as_of: date
) -> BankReportingPeriod:
    """A fresh snapshot row for ``as_of`` — the one construction site.

    Both writers (``pipeline._ensure_live_period`` and
    ``fact_derivation._ensure_period``) build the row here so the label and the
    ``period_start`` convention cannot drift apart between the live plane and
    the official-run plane, which address the same snapshot.
    """
    return BankReportingPeriod(
        organization_id=organization_id,
        bank_id=bank_id,
        period_start=as_of.replace(day=1),
        period_end=as_of,
        label=snapshot_label(as_of),
        status="open",
    )
