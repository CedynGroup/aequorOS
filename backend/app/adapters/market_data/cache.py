"""Market data cache and freshness rules (market_data_adapter.md §11.3/§11.4).

The cache is not a separate system: it is a well-known location on the
institution's ``canonical`` storage tier, accessed through the same
:class:`~app.storage.client.StorageClient` as everything else. Cached data is
authoritative when fresh; when stale, every use is tagged (never silently
substituted — §15) via :class:`StalenessTag`.
"""

from __future__ import annotations

import hashlib
import io
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from app.adapters.market_data.scope_taxonomy import DataScope, ScopeCategory, category_of
from app.storage.client import (
    ObjectMetadata,
    StorageClient,
    StorageLocation,
    StorageNotFoundError,
    StorageObject,
)
from app.storage.factory import get_storage_client

_CACHE_WRITER = "market-data-cache"

# Default freshness per §11.4. Yield curves and FX forwards are nominally
# ~1 day but their real bound is "until end of next business day", computed
# by :func:`fresh_until` with a business-day calendar; the timedelta here is
# the coarse fallback magnitude. FX spot: 1 hour during market hours;
# security master: 7 days; credit ratings: daily; macro forecasts: monthly.
FRESHNESS_BY_CATEGORY: dict[ScopeCategory, timedelta] = {
    ScopeCategory.YIELD_CURVE: timedelta(days=1),
    ScopeCategory.FX_SPOT: timedelta(hours=1),
    ScopeCategory.FX_FORWARD: timedelta(days=1),
    ScopeCategory.SECURITY_MASTER: timedelta(days=7),
    ScopeCategory.CREDIT_RATING: timedelta(days=1),
    ScopeCategory.MACRO_FORECAST: timedelta(days=30),
}

# Categories whose freshness bound is end of the next business day.
_BUSINESS_DAY_CATEGORIES = (ScopeCategory.YIELD_CURVE, ScopeCategory.FX_FORWARD)

_SATURDAY = 5  # date.weekday() value


def next_business_day(day: date) -> date:
    """The next Monday-to-Friday day strictly after ``day``.

    Weekend-only calendar for MVP; exchange holidays are a per-institution
    configuration concern deferred with the rest of configurable freshness
    (§11.4 "freshness thresholds are configurable per institution").
    """
    candidate = day + timedelta(days=1)
    while candidate.weekday() >= _SATURDAY:
        candidate += timedelta(days=1)
    return candidate


def fresh_until(category: ScopeCategory, pulled_at: datetime) -> datetime:
    """When data of ``category`` pulled at ``pulled_at`` stops being fresh.

    Yield curves and FX forwards are fresh until the end of the next business
    day after the pull (§11.4); everything else uses the fixed windows in
    :data:`FRESHNESS_BY_CATEGORY`.
    """
    if category in _BUSINESS_DAY_CATEGORIES:
        boundary_day = next_business_day(pulled_at.date())
        return datetime.combine(boundary_day, time(23, 59, 59), tzinfo=pulled_at.tzinfo)
    return pulled_at + FRESHNESS_BY_CATEGORY[category]


def is_fresh(pulled_at: datetime, category: ScopeCategory, now: datetime) -> bool:
    """Whether a pull from ``pulled_at`` is still authoritative at ``now``."""
    return now <= fresh_until(category, pulled_at)


@dataclass(frozen=True)
class StalenessTag:
    """Attribution attached to every cached value handed to a calculation.

    ``stale=True`` values may still be used (break-glass hierarchy, §10.6)
    but the tag must propagate into calculation output metadata so regulatory
    reports produced from stale data are clearly attributed (§11.5).
    """

    stale: bool
    age: timedelta
    source_batch_id: str | None


def staleness_tag(
    pulled_at: datetime,
    category: ScopeCategory,
    now: datetime,
    source_batch_id: str | None = None,
) -> StalenessTag:
    return StalenessTag(
        stale=not is_fresh(pulled_at, category, now),
        age=now - pulled_at,
        source_batch_id=source_batch_id,
    )


def cache_location(bank_slug: str, scope: DataScope) -> StorageLocation:
    """The well-known canonical-tier location holding the latest fresh value
    for one scope (§11.3)."""
    return StorageLocation(
        institution_slug=bank_slug,
        tier="canonical",
        object_path=f"market_data/cache/{scope.value}.json",
    )


def write_cache_entry(  # noqa: PLR0913
    bank_slug: str,
    scope: DataScope,
    *,
    as_of_date: date,
    values: dict[str, Any],
    pulled_at: datetime,
    source_batch_id: str,
    vendor: str,
    client: StorageClient | None = None,
) -> StorageObject:
    """Upsert the latest-fresh cache entry for ``scope``.

    The payload records the value, its as-of date, the pull that produced it,
    and the computed ``fresh_until`` bound — everything §11.3 requires for
    the cache to be authoritative-when-fresh and transparent-when-stale.
    """
    client = client or get_storage_client()
    category = category_of(scope)
    payload = {
        "scope": scope.value,
        "as_of_date": as_of_date.isoformat(),
        "values": values,
        "pulled_at": pulled_at.isoformat(),
        "fresh_until": fresh_until(category, pulled_at).isoformat(),
        "source_batch_id": source_batch_id,
        "vendor": vendor,
    }
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    location = cache_location(bank_slug, scope)
    metadata = ObjectMetadata(
        institution_slug=bank_slug,
        tier="canonical",
        checksum_sha256=hashlib.sha256(body).hexdigest(),
        written_at=datetime.now(UTC),
        written_by=_CACHE_WRITER,
        as_of_date=as_of_date.isoformat(),
        ingestion_batch_id=source_batch_id,
        source_system=vendor.upper(),
        source_reference=scope.value,
    )
    return client.write(location, io.BytesIO(body), metadata, content_type="application/json")


def read_cache_entry(
    bank_slug: str,
    scope: DataScope,
    client: StorageClient | None = None,
) -> dict[str, Any] | None:
    """The latest cache payload for ``scope``, or None when never cached.

    Callers judge freshness themselves via :func:`is_fresh` /
    :func:`staleness_tag` using the payload's ``pulled_at``.
    """
    client = client or get_storage_client()
    try:
        _, body = client.read(cache_location(bank_slug, scope))
    except StorageNotFoundError:
        return None
    payload = json.loads(body.read().decode("utf-8"))
    if not isinstance(payload, dict):
        return None
    return payload
