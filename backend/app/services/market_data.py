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

# Constructed-curve family a desk publishes for discounting (curve platform
# spec §6: OIS-based discounting separated from tenor projection curves).
DISCOUNT_CURVE_TYPE = "discount"


def desk_discount_curve_name(currency: str) -> str:
    """The desk's synthetic OIS / discounting proxy for ``currency``.

    Spec §8 naming grammar ``ISSUER.CURRENCY.SECTOR.TYPE`` — for GHS this is
    ``AEQ.GHS.OIS``, the Aequor Ghana Discounting Curve (AGD).
    """
    return f"AEQ.{currency.upper()}.OIS"


def desk_projection_curve_name(currency: str) -> str:
    """The desk's bootstrapped sovereign zero curve for ``currency``.

    Spec §8 naming grammar — for GHS this is ``AEQ.GHS.SOV.ZERO``, the Aequor
    Ghana Sovereign Curve (AGS), the preferred projection/transfer-pricing base.
    """
    return f"AEQ.{currency.upper()}.SOV.ZERO"


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
    # Source-preference graceful-fallback governance note (market_data_sources.md
    # §2). Default no-fallback so every existing view is byte-identical: the
    # preference-aware resolver sets these when the selected plane had no
    # servable row and the historical any-source arbitration served instead.
    fell_back: bool = False
    requested_source: str | None = None
    served_source: str | None = None


@dataclass(frozen=True)
class CurveView:
    """One currency's authoritative yield curve at an as-of date.

    ``points`` are ``(tenor_months, rate)`` pairs sorted by tenor; rates are
    decimal fractions (0.245, never 24.5) per data_engine.md §4.6.
    ``curve_type`` carries the constructed-curve family (``zero`` /
    ``forward`` / ``discount`` for desk curves, ``sovereign`` etc. for
    observed vendor curves) so consumers can badge each series truthfully.
    """

    currency: str
    curve_name: str
    curve_type: str
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


def _compose_overlay_points(  # noqa: PLR0913 - scope + tenant + curve key is the request
    db: Session,
    organization_id: str,
    bank_id: str,
    curve_name: str,
    as_of: date,
    points: tuple[tuple[int, Decimal], ...],
) -> tuple[tuple[int, Decimal], ...]:
    """Apply the bank's active curve overlays to ``points`` (identity if none).

    The read-time composition seam (market_data_overlays.md §2): golden
    canonical rows are never written; the adjusted series is derived on read.
    With no active overlay the base points are returned unchanged, so
    ``overlay=True`` on an unadjusted curve is byte-identical to ``overlay=False``.
    """
    # Local import breaks any import-order coupling (the overlays service imports
    # audit/schemas, never this module) — the same pattern list_yield_curves
    # uses for the entitlements service.
    from app.services import market_data_overlays  # noqa: PLC0415

    overlays = market_data_overlays.active_curve_overlays(
        db, organization_id, bank_id, as_of, curve_name=curve_name
    )
    composed = market_data_overlays.compose_curve(points, overlays)
    return composed.adjusted_points if composed is not None else points


def get_yield_curve(  # noqa: PLR0913 - scope + tenant + as-of is the request key
    db: Session,
    organization_id: str,
    bank_id: str,
    currency: str,
    as_of: date,
    *,
    curve_name: str | None = None,
    curve_type: str | None = None,
    source_systems: tuple[str, ...] | None = None,
    overlay: bool = False,
    now: datetime | None = None,
) -> CurveView | None:
    """The authoritative yield curve for ``currency`` at ``as_of``, or None.

    Latest business date at or before ``as_of`` wins; among same-date curves
    from different sources the most recently ingested wins (§15).

    ``curve_name`` and ``curve_type`` are OPT-IN narrowings for multi-curve
    consumers (the Markets tab serving every published curve by name; the
    dual-curve discount selection filtering to the ``discount`` family). When
    omitted the arbitration is exactly the historical
    single-curve-per-currency behavior that ``fact_derivation`` and the
    calculation engines depend on.

    ``source_systems`` and ``overlay`` are the source-preference seam
    (market_data_sources.md §3): when ``source_systems`` is ``None`` the query
    is unfiltered (byte-identical to the historical arbitration); when given,
    only rows from those source systems arbitrate. ``overlay=True`` composes
    the bank's active overlays onto the served points (identity when none).
    """
    now = now or utc_now()
    query = (
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
    if curve_name is not None:
        query = query.where(CanonicalYieldCurve.curve_name == curve_name)
    if curve_type is not None:
        query = query.where(CanonicalYieldCurve.curve_type == curve_type)
    if source_systems is not None:
        query = query.where(CanonicalYieldCurve.source_system.in_(source_systems))
    curve = db.scalar(query)
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
    resolved_points = tuple((int(tenor), Decimal(rate)) for tenor, rate in points)
    if overlay:
        resolved_points = _compose_overlay_points(
            db, organization_id, bank_id, curve.curve_name, as_of, resolved_points
        )
    return CurveView(
        currency=curve.currency,
        curve_name=curve.curve_name,
        curve_type=curve.curve_type,
        as_of_date=curve.as_of_date,
        points=resolved_points,
        attribution=_attribution(
            curve.ingested_at,
            curve.ingestion_batch_id,
            curve.source_system,
            ScopeCategory.YIELD_CURVE,
            now,
        ),
    )


def get_discount_curve(  # noqa: PLR0913 - scope + tenant + as-of is the request key
    db: Session,
    organization_id: str,
    bank_id: str,
    currency: str,
    as_of: date,
    *,
    source_systems: tuple[str, ...] | None = None,
    overlay: bool = False,
    now: datetime | None = None,
) -> CurveView | None:
    """The published DISCOUNTING curve for ``currency`` at ``as_of``, or None.

    Dual-curve selection (curve platform spec §6/§13 Stage 2 — discounting
    separated from projection): prefer the desk's synthetic OIS proxy named
    ``AEQ.{CCY}.OIS`` (the AGD for GHS); otherwise serve the latest
    current-generation curve published with ``curve_type='discount'`` under
    the same §15 arbitration as every other read.

    ``None`` is the graceful-fallback contract: no discounting curve is
    published for the currency, and every consumer MUST then discount on its
    projection/base curve — byte-identical to the historical single-curve
    behavior.
    """
    now = now or utc_now()
    preferred = get_yield_curve(
        db,
        organization_id,
        bank_id,
        currency,
        as_of,
        curve_name=desk_discount_curve_name(currency),
        source_systems=source_systems,
        overlay=overlay,
        now=now,
    )
    if preferred is not None:
        return preferred
    return get_yield_curve(
        db,
        organization_id,
        bank_id,
        currency,
        as_of,
        curve_type=DISCOUNT_CURVE_TYPE,
        source_systems=source_systems,
        overlay=overlay,
        now=now,
    )


def list_yield_curves(  # noqa: PLR0913 - scope + tenant + as-of is the request key
    db: Session,
    organization_id: str,
    bank_id: str,
    *,
    as_of: date,
    currency: str | None = None,
    source_systems: tuple[str, ...] | None = None,
    overlay: bool = False,
    now: datetime | None = None,
) -> list[CurveView]:
    """Every current-generation curve servable at ``as_of``, one per name.

    Multi-curve companion to :func:`get_yield_curve`: curves are grouped by
    ``(currency, curve_name)`` and each name is arbitrated independently
    (latest business date at or before ``as_of``, then most recently
    ingested) — so a zero, forward, and discounting curve for the same
    currency are all served side by side instead of collapsing to one
    winner per currency. Ordered by ``(currency, curve_name)``.

    ``source_systems`` restricts both discovery and per-name arbitration to
    the given planes (byte-identical when ``None``); ``overlay`` composes the
    bank's active overlays onto each served curve (spec §3).
    """
    now = now or utc_now()
    pair_query = (
        select(CanonicalYieldCurve.currency, CanonicalYieldCurve.curve_name)
        .where(
            CanonicalYieldCurve.organization_id == organization_id,
            CanonicalYieldCurve.bank_id == bank_id,
            CanonicalYieldCurve.as_of_date <= as_of,
            CanonicalYieldCurve.superseded_by.is_(None),
            CanonicalYieldCurve.validation_status.in_(_INCLUDED_VALIDATION_STATUSES),
        )
        .distinct()
    )
    if currency is not None:
        pair_query = pair_query.where(CanonicalYieldCurve.currency == currency.upper())
    if source_systems is not None:
        pair_query = pair_query.where(CanonicalYieldCurve.source_system.in_(source_systems))
    pairs = sorted((ccy, name) for ccy, name in db.execute(pair_query).all())
    # Spec §10: desk dataset entitlements gate visibility of AEQ.* curves.
    from app.services.market_desk import entitlements as desk_entitlements  # noqa: PLC0415

    datasets = desk_entitlements.active_datasets(db, organization_id, as_of=as_of)
    views: list[CurveView] = []
    for ccy, name in pairs:
        if name.startswith("AEQ.") and not desk_entitlements.curve_allowed(name, datasets):
            continue
        view = get_yield_curve(
            db,
            organization_id,
            bank_id,
            ccy,
            as_of,
            curve_name=name,
            source_systems=source_systems,
            overlay=overlay,
            now=now,
        )
        if view is not None:
            views.append(view)
    return views


def get_fx_spot(  # noqa: PLR0913 - scope + tenant + as-of is the request key
    db: Session,
    organization_id: str,
    bank_id: str,
    base_currency: str,
    quote_currency: str,
    as_of: date,
    *,
    source_systems: tuple[str, ...] | None = None,
    overlay: bool = False,
    now: datetime | None = None,
) -> FxRateView | None:
    """The authoritative spot for the pair at ``as_of`` (§15 arbitration).

    ``source_systems`` restricts arbitration to the given planes (byte-identical
    when ``None``). ``overlay`` is accepted for signature symmetry with the
    curve getters but is a no-op: overlays compose only onto curves
    (``base_ref_kind='curve'``), never spot FX.
    """
    del overlay  # FX carries no overlay composition; parameter kept for symmetry.
    now = now or utc_now()
    query = _fx_spot_query(organization_id, bank_id, base_currency, quote_currency, as_of)
    if source_systems is not None:
        query = query.where(CanonicalFxRate.source_system.in_(source_systems))
    row = db.scalar(
        query.order_by(
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
    organization_id: str,
    bank_id: str,
    base_currency: str,
    quote_currency: str,
    as_of: date,
    limit: int = DEFAULT_FX_HISTORY_LIMIT,
    *,
    source_systems: tuple[str, ...] | None = None,
) -> list[tuple[date, Decimal]]:
    """Persisted spot observations for the pair, ascending by business date.

    Historical FX is not a scope: it is derived from persisted spot pulls
    over time (§5.2). One observation per business date — the most recently
    ingested current-generation row wins — capped to the most recent
    ``limit`` dates at or before ``as_of``. Feeds the VaR return series.

    ``source_systems`` restricts the series to the given planes (byte-identical
    when ``None``).
    """
    query = _fx_spot_query(organization_id, bank_id, base_currency, quote_currency, as_of)
    if source_systems is not None:
        query = query.where(CanonicalFxRate.source_system.in_(source_systems))
    rows = db.scalars(
        query.order_by(
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
    organization_id: str,
    bank_id: str,
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
    organization_id: str,
    bank_id: str,
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
    organization_id: str,
    bank_id: str,
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
    organization_id: str,
    bank_id: str,
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
    organization_id: str,
    bank_id: str,
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
    organization_id: str,
    bank_id: str,
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
    organization_id: str,
    bank_id: str,
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
    organization_id: str,
    bank_id: str,
    index_code: str,
    as_of: date,
    scenario: str = "base",
    *,
    source_systems: tuple[str, ...] | None = None,
    overlay: bool = False,
    now: datetime | None = None,
) -> IndexView | None:
    """The authoritative index/forecast value for ``index_code`` at ``as_of``.

    ``source_systems`` restricts arbitration to the given planes (byte-identical
    when ``None``). ``overlay`` is accepted for signature symmetry but is a
    no-op: overlays compose only onto curves, never reference indices.
    """
    del overlay  # Indices carry no overlay composition; parameter kept for symmetry.
    from app.services.market_desk import entitlements as desk_entitlements  # noqa: PLC0415

    datasets = desk_entitlements.active_datasets(db, organization_id, as_of=as_of)
    if not desk_entitlements.index_allowed(index_code, datasets):
        return None
    now = now or utc_now()
    query = (
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
    if source_systems is not None:
        query = query.where(CanonicalMarketIndex.source_system.in_(source_systems))
    row = db.scalar(query)
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
