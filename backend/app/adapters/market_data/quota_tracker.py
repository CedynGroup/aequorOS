"""Per-institution quota accounting (market_data_adapter.md §11.1).

MVP scope per §16.5: tracking and estimation ONLY. The tracker counts units,
estimates monthly consumption before a pull, and reports when an estimate
exceeds the configured cap — as a warning-level signal (``within_cap=False``),
never by raising or blocking. Enforcement policies (blocking, capping,
override workflows, §11.2) are Phase 2.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from app.adapters.market_data.base import QuotaEstimate
from app.adapters.market_data.scope_taxonomy import DataScope, PullFrequency
from app.adapters.market_data.scope_translator import Catalog, quota_units
from app.models.market_data import MarketDataQuotaUsage

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.orm import Session

# Monthly pull counts per frequency (§9.2 defaults): HOURLY pulls run during
# business hours only (roughly 8 pulls/day) across ~22 business days;
# END_OF_DAY once per business day; ON_DEMAND is estimated as a single pull.
BUSINESS_DAYS_PER_MONTH = 22
HOURLY_PULLS_PER_BUSINESS_DAY = 8

PULLS_PER_MONTH_BY_FREQUENCY: dict[PullFrequency, int] = {
    PullFrequency.ON_DEMAND: 1,
    PullFrequency.HOURLY: HOURLY_PULLS_PER_BUSINESS_DAY * BUSINESS_DAYS_PER_MONTH,
    PullFrequency.END_OF_DAY: BUSINESS_DAYS_PER_MONTH,
    PullFrequency.WEEKLY: 4,
    PullFrequency.MONTHLY: 1,
}


def month_key(when: datetime) -> str:
    """The ``YYYY-MM`` accounting month a timestamp belongs to (UTC)."""
    return when.astimezone(UTC).strftime("%Y-%m")


def estimate(
    catalog: Catalog,
    scopes: list[DataScope],
    frequency: PullFrequency,
    current_consumption: int,
    cap: int | None,
) -> QuotaEstimate:
    """Pre-flight quota estimate for pulling ``scopes`` at ``frequency``.

    ``within_cap=False`` is a warning to surface to the bank ("selected
    scopes will consume approximately N units per pull, roughly M units per
    month"), not a refusal — this function never raises for over-cap
    estimates (§16.5). ``cap=None`` means no cap is configured and the
    estimate is always within cap.
    """
    units_per_pull = quota_units(catalog, scopes)
    monthly_units = units_per_pull * PULLS_PER_MONTH_BY_FREQUENCY[frequency]
    within_cap = cap is None or current_consumption + monthly_units <= cap
    return QuotaEstimate(
        scopes=list(scopes),
        frequency=frequency,
        estimated_units_per_pull=units_per_pull,
        estimated_monthly_units=monthly_units,
        current_monthly_consumption=current_consumption,
        monthly_cap=cap,
        within_cap=within_cap,
    )


def record_consumption(  # noqa: PLR0913
    db: Session,
    organization_id: UUID,
    bank_id: UUID,
    vendor: str,
    units: int,
    when: datetime | None = None,
) -> None:
    """Record actual post-pull consumption against the pull's calendar month.

    Upserts the ``market_data_quota_usage`` row keyed
    (org, bank, vendor, month): increments ``units_consumed`` by ``units``
    and ``pull_count`` by one. Adapters must report consumption honestly
    (§4.2) — this is the single write path for quota accounting.
    """
    when = when or datetime.now(UTC)
    month = month_key(when)
    row = (
        db.query(MarketDataQuotaUsage)
        .filter(
            MarketDataQuotaUsage.organization_id == organization_id,
            MarketDataQuotaUsage.bank_id == bank_id,
            MarketDataQuotaUsage.vendor == vendor,
            MarketDataQuotaUsage.month == month,
        )
        .one_or_none()
    )
    if row is None:
        row = MarketDataQuotaUsage(
            organization_id=organization_id,
            bank_id=bank_id,
            vendor=vendor,
            month=month,
            units_consumed=units,
            pull_count=1,
        )
        db.add(row)
    else:
        row.units_consumed = row.units_consumed + units
        row.pull_count = row.pull_count + 1
    db.flush()


def current_month_usage(
    db: Session,
    organization_id: UUID,
    bank_id: UUID,
    vendor: str,
    when: datetime | None = None,
) -> int:
    """Units consumed by (org, bank, vendor) in the current accounting month."""
    when = when or datetime.now(UTC)
    row = (
        db.query(MarketDataQuotaUsage)
        .filter(
            MarketDataQuotaUsage.organization_id == organization_id,
            MarketDataQuotaUsage.bank_id == bank_id,
            MarketDataQuotaUsage.vendor == vendor,
            MarketDataQuotaUsage.month == month_key(when),
        )
        .one_or_none()
    )
    return 0 if row is None else int(row.units_consumed)
