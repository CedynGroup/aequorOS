"""Vendor-blind market data consumption by business scope.

Calculation modules request market data by business meaning — a currency's
yield curve, an FX pair's spot, an issuer's rating — plus an as-of date and
the institution, never by vendor concept (market_data_adapter.md §5, §15).
Reads come from the canonical market-data entities the adapters persist
(``app.models.canonical``), so every answer is multi-source-aware:

- **Current generation only** — ``superseded_by IS NULL`` (§4.3 idempotent
  re-pull semantics).
- **Latest as-of wins** — among generations at or before the requested
  as-of date, the newest business date is authoritative.
- **Most-recently-refreshed wins** — when two sources cover the same scope
  for the same business date (e.g. two vendors naming the same curve
  differently), the row with the newest ``ingested_at`` is served (§15
  Phase-1 arbitration; consensus is Phase 3).

Every view carries a :class:`SourceAttribution` with the freshness verdict
per §11.4, so no stale value is ever used silently (§15): callers propagate
``stale`` into calculation output metadata.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.market_data.cache import is_fresh
from app.adapters.market_data.scope_taxonomy import ScopeCategory
from app.db.base import utc_now
from app.models import (
    CanonicalCounterpartyRating,
    CanonicalFxRate,
    CanonicalMarketIndex,
    CanonicalYieldCurve,
    CanonicalYieldCurvePoint,
)

# Validation statuses a calculation may consume (mirrors fact_derivation).
_INCLUDED_VALIDATION_STATUSES = ("accepted", "warning")

# 251 spot observations yield the 250 daily returns the FX VaR window uses.
DEFAULT_FX_HISTORY_LIMIT = 251


def _as_aware(value: datetime) -> datetime:
    """Normalize a possibly-naive DB datetime (SQLite round-trip) to UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


@dataclass(frozen=True)
class SourceAttribution:
    """Provenance + freshness attribution carried by every market data view.

    ``source_system`` is the only vendor-shaped field on the API surface and
    exists purely for attribution in calculation output metadata; ``stale``
    must propagate into any output computed from this view (§11.5, §15).
    """

    source_system: str
    ingestion_batch_id: UUID
    ingested_at: datetime
    stale: bool
    age_seconds: float


@dataclass(frozen=True)
class CurveView:
    """One currency's authoritative yield curve at an as-of date.

    ``points`` are ``(tenor_months, rate)`` pairs sorted by tenor; rates are
    decimal fractions (0.245, never 24.5) per data_engine.md §4.6.
    """

    currency: str
    curve_name: str
    as_of_date: date
    points: tuple[tuple[int, Decimal], ...]
    attribution: SourceAttribution


@dataclass(frozen=True)
class FxRateView:
    """The authoritative spot for one pair: ``rate`` quote units per 1 base."""

    base_currency: str
    quote_currency: str
    rate: Decimal
    as_of_date: date
    attribution: SourceAttribution


@dataclass(frozen=True)
class RatingView:
    issuer: str
    agency: str
    rating: str
    watch_status: str | None
    rating_date: date
    as_of_date: date
    attribution: SourceAttribution


@dataclass(frozen=True)
class IndexView:
    index_code: str
    value: Decimal
    scenario: str
    horizon_months: int | None
    as_of_date: date
    attribution: SourceAttribution


def _attribution(
    ingested_at: datetime,
    ingestion_batch_id: UUID,
    source_system: str,
    category: ScopeCategory,
    now: datetime,
) -> SourceAttribution:
    pulled_at = _as_aware(ingested_at)
    return SourceAttribution(
        source_system=source_system,
        ingestion_batch_id=ingestion_batch_id,
        ingested_at=pulled_at,
        stale=not is_fresh(pulled_at, category, now),
        age_seconds=(now - pulled_at).total_seconds(),
    )


def get_yield_curve(  # noqa: PLR0913 - scope + tenant + as-of is the request key
    db: Session,
    organization_id: UUID,
    bank_id: UUID,
    currency: str,
    as_of: date,
    *,
    now: datetime | None = None,
) -> CurveView | None:
    """The authoritative yield curve for ``currency`` at ``as_of``, or None.

    Latest business date at or before ``as_of`` wins; among same-date curves
    from different sources the most recently ingested wins (§15).
    """
    now = now or utc_now()
    curve = db.scalar(
        select(CanonicalYieldCurve)
        .where(
            CanonicalYieldCurve.organization_id == organization_id,
            CanonicalYieldCurve.bank_id == bank_id,
            CanonicalYieldCurve.currency == currency.upper(),
            CanonicalYieldCurve.as_of_date <= as_of,
            CanonicalYieldCurve.superseded_by.is_(None),
            CanonicalYieldCurve.validation_status.in_(_INCLUDED_VALIDATION_STATUSES),
        )
        .order_by(
            CanonicalYieldCurve.as_of_date.desc(),
            CanonicalYieldCurve.ingested_at.desc(),
            CanonicalYieldCurve.id.desc(),
        )
        .limit(1)
    )
    if curve is None:
        return None
    points = list(
        db.execute(
            select(CanonicalYieldCurvePoint.tenor_months, CanonicalYieldCurvePoint.rate)
            .where(
                CanonicalYieldCurvePoint.organization_id == organization_id,
                CanonicalYieldCurvePoint.yield_curve_id == curve.id,
                CanonicalYieldCurvePoint.superseded_by.is_(None),
                CanonicalYieldCurvePoint.validation_status.in_(_INCLUDED_VALIDATION_STATUSES),
            )
            .order_by(CanonicalYieldCurvePoint.tenor_months)
        ).all()
    )
    if not points:
        return None
    return CurveView(
        currency=curve.currency,
        curve_name=curve.curve_name,
        as_of_date=curve.as_of_date,
        points=tuple((int(tenor), Decimal(rate)) for tenor, rate in points),
        attribution=_attribution(
            curve.ingested_at,
            curve.ingestion_batch_id,
            curve.source_system,
            ScopeCategory.YIELD_CURVE,
            now,
        ),
    )


def get_fx_spot(  # noqa: PLR0913 - scope + tenant + as-of is the request key
    db: Session,
    organization_id: UUID,
    bank_id: UUID,
    base_currency: str,
    quote_currency: str,
    as_of: date,
    *,
    now: datetime | None = None,
) -> FxRateView | None:
    """The authoritative spot for the pair at ``as_of`` (§15 arbitration)."""
    now = now or utc_now()
    row = db.scalar(
        _fx_spot_query(organization_id, bank_id, base_currency, quote_currency, as_of)
        .order_by(
            CanonicalFxRate.as_of_date.desc(),
            CanonicalFxRate.ingested_at.desc(),
            CanonicalFxRate.id.desc(),
        )
        .limit(1)
    )
    if row is None:
        return None
    return FxRateView(
        base_currency=row.base_currency,
        quote_currency=row.quote_currency,
        rate=Decimal(row.rate),
        as_of_date=row.as_of_date,
        attribution=_attribution(
            row.ingested_at,
            row.ingestion_batch_id,
            row.source_system,
            ScopeCategory.FX_SPOT,
            now,
        ),
    )


def get_fx_spot_history(  # noqa: PLR0913 - scope + tenant + as-of is the request key
    db: Session,
    organization_id: UUID,
    bank_id: UUID,
    base_currency: str,
    quote_currency: str,
    as_of: date,
    limit: int = DEFAULT_FX_HISTORY_LIMIT,
) -> list[tuple[date, Decimal]]:
    """Persisted spot observations for the pair, ascending by business date.

    Historical FX is not a scope: it is derived from persisted spot pulls
    over time (§5.2). One observation per business date — the most recently
    ingested current-generation row wins — capped to the most recent
    ``limit`` dates at or before ``as_of``. Feeds the VaR return series.
    """
    rows = db.scalars(
        _fx_spot_query(organization_id, bank_id, base_currency, quote_currency, as_of).order_by(
            CanonicalFxRate.as_of_date.asc(),
            CanonicalFxRate.ingested_at.desc(),
            CanonicalFxRate.id.desc(),
        )
    ).all()
    by_date: dict[date, Decimal] = {}
    for row in rows:
        # First row per date carries the newest ingested_at (query ordering).
        by_date.setdefault(row.as_of_date, Decimal(row.rate))
    series = sorted(by_date.items())
    return series[-limit:] if limit > 0 else series


def _fx_spot_query(
    organization_id: UUID,
    bank_id: UUID,
    base_currency: str,
    quote_currency: str,
    as_of: date,
):
    return select(CanonicalFxRate).where(
        CanonicalFxRate.organization_id == organization_id,
        CanonicalFxRate.bank_id == bank_id,
        CanonicalFxRate.base_currency == base_currency.upper(),
        CanonicalFxRate.quote_currency == quote_currency.upper(),
        CanonicalFxRate.rate_type == "spot",
        CanonicalFxRate.as_of_date <= as_of,
        CanonicalFxRate.superseded_by.is_(None),
        CanonicalFxRate.validation_status.in_(_INCLUDED_VALIDATION_STATUSES),
    )


def list_fx_base_currencies(
    db: Session,
    organization_id: UUID,
    bank_id: UUID,
    quote_currency: str,
    as_of: date,
) -> list[str]:
    """Base currencies with at least one persisted spot against ``quote_currency``.

    Lets a consumer discover which pairs the canonical store can answer for
    without naming any vendor concept.
    """
    quote = quote_currency.upper()
    rows = db.scalars(
        select(CanonicalFxRate.base_currency)
        .where(
            CanonicalFxRate.organization_id == organization_id,
            CanonicalFxRate.bank_id == bank_id,
            CanonicalFxRate.quote_currency == quote,
            CanonicalFxRate.rate_type == "spot",
            CanonicalFxRate.as_of_date <= as_of,
            CanonicalFxRate.superseded_by.is_(None),
            CanonicalFxRate.validation_status.in_(_INCLUDED_VALIDATION_STATUSES),
        )
        .distinct()
    ).all()
    return sorted(currency for currency in rows if currency != quote)


# ---------------------------------------------------------------------------
# Scope discovery for the consumption views: which scopes can the canonical
# store answer at an as-of date? Same servability filters as the getters
# (current generation, accepted/warning, business date at or before as-of);
# each discovered key is then served through the arbitrating getter above.
# ---------------------------------------------------------------------------


def list_curve_currencies(
    db: Session,
    organization_id: UUID,
    bank_id: UUID,
    as_of: date,
) -> list[str]:
    """Currencies with at least one servable yield curve at ``as_of``."""
    rows = db.scalars(
        select(CanonicalYieldCurve.currency)
        .where(
            CanonicalYieldCurve.organization_id == organization_id,
            CanonicalYieldCurve.bank_id == bank_id,
            CanonicalYieldCurve.as_of_date <= as_of,
            CanonicalYieldCurve.superseded_by.is_(None),
            CanonicalYieldCurve.validation_status.in_(_INCLUDED_VALIDATION_STATUSES),
        )
        .distinct()
    ).all()
    return sorted(rows)


def list_fx_pairs(
    db: Session,
    organization_id: UUID,
    bank_id: UUID,
    as_of: date,
) -> list[tuple[str, str]]:
    """Distinct (base, quote) spot pairs servable at ``as_of``."""
    rows = db.execute(
        select(CanonicalFxRate.base_currency, CanonicalFxRate.quote_currency)
        .where(
            CanonicalFxRate.organization_id == organization_id,
            CanonicalFxRate.bank_id == bank_id,
            CanonicalFxRate.rate_type == "spot",
            CanonicalFxRate.as_of_date <= as_of,
            CanonicalFxRate.superseded_by.is_(None),
            CanonicalFxRate.validation_status.in_(_INCLUDED_VALIDATION_STATUSES),
        )
        .distinct()
    ).all()
    return sorted((base, quote) for base, quote in rows)


def list_rating_issuers(
    db: Session,
    organization_id: UUID,
    bank_id: UUID,
    as_of: date,
) -> list[str]:
    """Issuers with at least one servable rating observation at ``as_of``."""
    rows = db.scalars(
        select(CanonicalCounterpartyRating.issuer)
        .where(
            CanonicalCounterpartyRating.organization_id == organization_id,
            CanonicalCounterpartyRating.bank_id == bank_id,
            CanonicalCounterpartyRating.as_of_date <= as_of,
            CanonicalCounterpartyRating.superseded_by.is_(None),
            CanonicalCounterpartyRating.validation_status.in_(_INCLUDED_VALIDATION_STATUSES),
        )
        .distinct()
    ).all()
    return sorted(rows)


def list_index_scopes(
    db: Session,
    organization_id: UUID,
    bank_id: UUID,
    as_of: date,
) -> list[tuple[str, str]]:
    """Distinct (index_code, scenario) pairs servable at ``as_of``."""
    rows = db.execute(
        select(CanonicalMarketIndex.index_code, CanonicalMarketIndex.scenario)
        .where(
            CanonicalMarketIndex.organization_id == organization_id,
            CanonicalMarketIndex.bank_id == bank_id,
            CanonicalMarketIndex.as_of_date <= as_of,
            CanonicalMarketIndex.superseded_by.is_(None),
            CanonicalMarketIndex.validation_status.in_(_INCLUDED_VALIDATION_STATUSES),
        )
        .distinct()
    ).all()
    return sorted((code, scenario) for code, scenario in rows)


def get_rating(  # noqa: PLR0913 - scope + tenant + as-of is the request key
    db: Session,
    organization_id: UUID,
    bank_id: UUID,
    issuer: str,
    as_of: date,
    *,
    now: datetime | None = None,
) -> RatingView | None:
    """The authoritative rating observation for ``issuer`` at ``as_of``."""
    now = now or utc_now()
    row = db.scalar(
        select(CanonicalCounterpartyRating)
        .where(
            CanonicalCounterpartyRating.organization_id == organization_id,
            CanonicalCounterpartyRating.bank_id == bank_id,
            CanonicalCounterpartyRating.issuer == issuer,
            CanonicalCounterpartyRating.as_of_date <= as_of,
            CanonicalCounterpartyRating.superseded_by.is_(None),
            CanonicalCounterpartyRating.validation_status.in_(_INCLUDED_VALIDATION_STATUSES),
        )
        .order_by(
            CanonicalCounterpartyRating.as_of_date.desc(),
            CanonicalCounterpartyRating.ingested_at.desc(),
            CanonicalCounterpartyRating.id.desc(),
        )
        .limit(1)
    )
    if row is None:
        return None
    return RatingView(
        issuer=row.issuer,
        agency=row.agency,
        rating=row.rating,
        watch_status=row.watch_status,
        rating_date=row.rating_date,
        as_of_date=row.as_of_date,
        attribution=_attribution(
            row.ingested_at,
            row.ingestion_batch_id,
            row.source_system,
            ScopeCategory.CREDIT_RATING,
            now,
        ),
    )


def get_index(  # noqa: PLR0913 - scope + tenant + as-of is the request key
    db: Session,
    organization_id: UUID,
    bank_id: UUID,
    index_code: str,
    as_of: date,
    scenario: str = "base",
    *,
    now: datetime | None = None,
) -> IndexView | None:
    """The authoritative index/forecast value for ``index_code`` at ``as_of``."""
    now = now or utc_now()
    row = db.scalar(
        select(CanonicalMarketIndex)
        .where(
            CanonicalMarketIndex.organization_id == organization_id,
            CanonicalMarketIndex.bank_id == bank_id,
            CanonicalMarketIndex.index_code == index_code,
            CanonicalMarketIndex.scenario == scenario,
            CanonicalMarketIndex.as_of_date <= as_of,
            CanonicalMarketIndex.superseded_by.is_(None),
            CanonicalMarketIndex.validation_status.in_(_INCLUDED_VALIDATION_STATUSES),
        )
        .order_by(
            CanonicalMarketIndex.as_of_date.desc(),
            CanonicalMarketIndex.ingested_at.desc(),
            CanonicalMarketIndex.id.desc(),
        )
        .limit(1)
    )
    if row is None:
        return None
    return IndexView(
        index_code=row.index_code,
        value=Decimal(row.value),
        scenario=row.scenario,
        horizon_months=row.horizon_months,
        as_of_date=row.as_of_date,
        attribution=_attribution(
            row.ingested_at,
            row.ingestion_batch_id,
            row.source_system,
            ScopeCategory.MACRO_FORECAST,
            now,
        ),
    )
