"""Per-bank market-data source preference: resolution, arbitration, planes, forward grid.

The service half of the three-source model (market_data_sources.md §2–§4). It

- resolves a bank's per-category (curves/fx/rates) selection of base plane
  (aequor/bank/vendor) + overlay toggle, synthesising the spec §2 default
  (aequor + overlay on) when no row exists;
- exposes preference-aware resolver functions the calculation path calls so the
  bank's selection flows live into IRRBB/FTP, implementing the mandatory §2
  graceful fallback (a gap in the preferred plane never breaks a calculation —
  the historical any-source arbitration serves and the view is flagged
  ``fell_back``);
- builds the §4 side-by-side ``/planes`` comparison and the FC-5/G1 published
  forward grid.

The category → ``source_system`` mapping (spec §2) is the single source of truth
for what each plane means; the arbitration getters in ``market_data`` do the
actual filtering.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select, union_all

from app.models import (
    Bank,
    CanonicalFxRate,
    CanonicalMarketIndex,
    CanonicalYieldCurve,
    CanonicalYieldCurvePoint,
    DeskDetermination,
    MarketDataOverlay,
    MarketDataSourcePreference,
)
from app.schemas.market_data_sources import (
    Category,
    CategoryPreferenceRead,
    ForwardGridAssumptionsRead,
    ForwardGridPillarRead,
    ForwardGridRead,
    ForwardGridRowRead,
    PlaneAttributionRead,
    PlaneCurveItemRead,
    PlaneCurvePointRead,
    PlaneFxItemRead,
    PlaneIndexItemRead,
    PlaneItemRead,
    PlaneOverlayDeltaRead,
    PlaneOverlayRead,
    PlaneRead,
    PlanesRead,
    Source,
    SourcePreferencesRead,
    SourcePreferencesUpdate,
)
from app.services import market_data, market_data_overlays
from app.services.audit import record_event
from app.services.market_data import (
    CurveView,
    FxRateView,
    IndexView,
    SourceAttribution,
    desk_projection_curve_name,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.api.deps import TenantContext

# Category → source_system set (spec §2). ``aequor`` and ``vendor`` are fixed;
# ``bank`` is everything else present for the bank (NOT aequor, NOT vendor).
AEQUOR_SOURCE_SYSTEMS: tuple[str, ...] = ("AEQUOR_DESK",)
VENDOR_SOURCE_SYSTEMS: tuple[str, ...] = ("BLOOMBERG", "REFINITIV")
CATEGORY_SOURCES: dict[str, tuple[str, ...]] = {
    "aequor": AEQUOR_SOURCE_SYSTEMS,
    "vendor": VENDOR_SOURCE_SYSTEMS,
}
_NON_BANK_SOURCE_SYSTEMS: frozenset[str] = frozenset(AEQUOR_SOURCE_SYSTEMS) | frozenset(
    VENDOR_SOURCE_SYSTEMS
)

CATEGORIES: tuple[Category, ...] = ("curves", "fx", "rates")
SOURCE_CHOICES: tuple[Source, ...] = ("aequor", "bank", "vendor")
DEFAULT_SOURCE: Source = "aequor"
DEFAULT_OVERLAY = True

_INCLUDED_VALIDATION_STATUSES = ("accepted", "warning")
_PUBLISHED_DETERMINATION_STATUSES = ("approved", "published")


def resolve_source_systems(source: str, present: set[str]) -> tuple[str, ...]:
    """The ``source_system`` set a category source resolves to (spec §2 table).

    ``aequor`` → ``('AEQUOR_DESK',)``; ``vendor`` → ``('BLOOMBERG','REFINITIV')``
    (both fixed, independent of what the bank has); ``bank`` → every source
    system present for the bank that is neither aequor nor vendor, sorted.
    """
    if source in CATEGORY_SOURCES:
        return CATEGORY_SOURCES[source]
    if source == "bank":
        return tuple(sorted(s for s in present if s not in _NON_BANK_SOURCE_SYSTEMS))
    raise ValueError(f"Unknown market-data source {source!r}; expected one of {SOURCE_CHOICES}.")


# ---------------------------------------------------------------------------
# Preference resolution
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CategoryChoice:
    """One category's resolved selection."""

    source: Source
    overlay: bool


@dataclass(frozen=True)
class ResolvedPreference:
    """A bank's full preference (row values, or the synthesised default)."""

    curves: CategoryChoice
    fx: CategoryChoice
    rates: CategoryChoice
    updated_at: Any | None
    updated_by: UUID | None

    def choice(self, category: str) -> CategoryChoice:
        return getattr(self, category)


def _resolve_row(row: MarketDataSourcePreference | None) -> ResolvedPreference:
    if row is None:
        default = CategoryChoice(DEFAULT_SOURCE, DEFAULT_OVERLAY)
        return ResolvedPreference(default, default, default, None, None)
    # DB values are CHECK-constrained to the source literals; narrow for typing.
    return ResolvedPreference(
        curves=CategoryChoice(cast("Source", row.curves_source), row.curves_overlay),
        fx=CategoryChoice(cast("Source", row.fx_source), row.fx_overlay),
        rates=CategoryChoice(cast("Source", row.rates_source), row.rates_overlay),
        updated_at=row.updated_at,
        updated_by=row.updated_by,
    )


def _load_preference_row(
    db: Session, organization_id: str, bank_id: str
) -> MarketDataSourcePreference | None:
    return db.scalar(
        select(MarketDataSourcePreference).where(
            MarketDataSourcePreference.organization_id == organization_id,
            MarketDataSourcePreference.bank_id == bank_id,
        )
    )


def load_preference(db: Session, organization_id: str, bank_id: str) -> ResolvedPreference:
    """The bank's resolved preference (defaults synthesised when no row)."""
    return _resolve_row(_load_preference_row(db, organization_id, bank_id))


def get_preference(db: Session, ctx: TenantContext, bank_id: str) -> SourcePreferencesRead:
    """API read: the resolved preference (spec §4 GET)."""
    _get_bank_or_404(db, ctx, bank_id)
    return _to_read(bank_id, load_preference(db, ctx.organization_id, bank_id))


def set_preference(
    db: Session, ctx: TenantContext, bank_id: str, patch: SourcePreferencesUpdate
) -> SourcePreferencesRead:
    """API upsert: apply a partial patch, audit it, return the resolved row (spec §4 PUT)."""
    _get_bank_or_404(db, ctx, bank_id)
    row = _load_preference_row(db, ctx.organization_id, bank_id)
    created = row is None
    if row is None:
        row = MarketDataSourcePreference(
            organization_id=ctx.organization_id, bank_id=bank_id
        )  # source/overlay columns fall to their aequor/on defaults on insert
        db.add(row)
    for category in CATEGORIES:
        cat_patch = getattr(patch, category)
        if cat_patch is None:
            continue
        if cat_patch.source is not None:
            setattr(row, f"{category}_source", cat_patch.source)
        if cat_patch.overlay is not None:
            setattr(row, f"{category}_overlay", cat_patch.overlay)
    row.updated_by = ctx.actor_user_id
    db.flush()
    record_event(
        db,
        ctx,
        event_type="market_data_source_preference.updated",
        entity_type="market_data_source_preference",
        entity_id=row.id,
        details={
            "bank_id": bank_id,
            "created": created,
            "curves_source": row.curves_source,
            "curves_overlay": row.curves_overlay,
            "fx_source": row.fx_source,
            "fx_overlay": row.fx_overlay,
            "rates_source": row.rates_source,
            "rates_overlay": row.rates_overlay,
            "reason": patch.reason,
        },
    )
    db.commit()
    return _to_read(bank_id, _resolve_row(row))


def _to_read(bank_id: str, resolved: ResolvedPreference) -> SourcePreferencesRead:
    return SourcePreferencesRead(
        bank_id=bank_id,
        curves=CategoryPreferenceRead(
            source=resolved.curves.source, overlay=resolved.curves.overlay
        ),
        fx=CategoryPreferenceRead(source=resolved.fx.source, overlay=resolved.fx.overlay),
        rates=CategoryPreferenceRead(source=resolved.rates.source, overlay=resolved.rates.overlay),
        updated_at=resolved.updated_at,
        updated_by=resolved.updated_by,
    )


# ---------------------------------------------------------------------------
# Preference-aware resolvers (the calculation path — spec §3)
# ---------------------------------------------------------------------------


def _flag_fallback[ViewT: (CurveView, FxRateView, IndexView)](
    view: ViewT, requested_source: str
) -> ViewT:
    """Mark a view served by the historical any-source arbitration (spec §2).

    Views share the ``SourceAttribution`` field, so fallback-flagging preserves
    the concrete view type (curve/fx/index) for the caller.
    """
    attribution: SourceAttribution = view.attribution
    served = replace(
        attribution,
        fell_back=True,
        requested_source=requested_source,
        served_source=attribution.source_system,
    )
    return replace(view, attribution=served)


def preferred_projection_curve(  # noqa: PLR0913 - scope + tenant + as-of is the request key
    db: Session,
    organization_id: str,
    bank_id: str,
    currency: str,
    as_of: date,
    *,
    now: Any | None = None,
) -> CurveView | None:
    """The base yield curve FTP consumes, under the bank's ``curves`` preference.

    Mirrors the historical two-step (desk sovereign zero by name, else any
    curve) but filtered to the selected plane, with the §2 graceful fallback to
    the any-source arbitration when the selected plane has no servable curve.
    """
    choice = load_preference(db, organization_id, bank_id).curves
    resolved = resolve_source_systems(
        choice.source, _present_sources(db, organization_id, bank_id, "curves", as_of)
    )
    projection = desk_projection_curve_name(currency)
    if resolved:
        view = market_data.get_yield_curve(
            db,
            organization_id,
            bank_id,
            currency,
            as_of,
            curve_name=projection,
            source_systems=resolved,
            overlay=choice.overlay,
            now=now,
        )
        if view is None:
            view = market_data.get_yield_curve(
                db,
                organization_id,
                bank_id,
                currency,
                as_of,
                source_systems=resolved,
                overlay=choice.overlay,
                now=now,
            )
        if view is not None:
            return view
    # Graceful fallback: the historical any-source arbitration (spec §2).
    view = market_data.get_yield_curve(
        db,
        organization_id,
        bank_id,
        currency,
        as_of,
        curve_name=projection,
        overlay=choice.overlay,
        now=now,
    )
    if view is None:
        view = market_data.get_yield_curve(
            db, organization_id, bank_id, currency, as_of, overlay=choice.overlay, now=now
        )
    return _flag_fallback(view, choice.source) if view is not None else None


def preferred_discount_curve(  # noqa: PLR0913 - scope + tenant + as-of is the request key
    db: Session,
    organization_id: str,
    bank_id: str,
    currency: str,
    as_of: date,
    *,
    now: Any | None = None,
) -> CurveView | None:
    """The discounting curve the IRRBB EVE engine consumes (``curves`` preference)."""
    choice = load_preference(db, organization_id, bank_id).curves
    resolved = resolve_source_systems(
        choice.source, _present_sources(db, organization_id, bank_id, "curves", as_of)
    )
    if resolved:
        view = market_data.get_discount_curve(
            db,
            organization_id,
            bank_id,
            currency,
            as_of,
            source_systems=resolved,
            overlay=choice.overlay,
            now=now,
        )
        if view is not None:
            return view
    view = market_data.get_discount_curve(
        db, organization_id, bank_id, currency, as_of, overlay=choice.overlay, now=now
    )
    return _flag_fallback(view, choice.source) if view is not None else None


@dataclass(frozen=True)
class PrefetchedPreferredCurves:
    """Request-local projection and discount curves, keyed by effective date."""

    projection: dict[date, CurveView | None]
    discount: dict[date, CurveView | None]


def _curve_sources_by_date(
    db: Session,
    organization_id: str,
    bank_id: str,
    source: str,
    dates: list[date],
) -> dict[date, tuple[str, ...]]:
    if source != "bank":
        resolved = resolve_source_systems(source, set())
        return dict.fromkeys(dates, resolved)
    source_rows = db.execute(
        select(
            CanonicalYieldCurve.source_system,
            func.min(CanonicalYieldCurve.as_of_date).label("first_seen"),
        )
        .where(
            CanonicalYieldCurve.organization_id == organization_id,
            CanonicalYieldCurve.bank_id == bank_id,
            CanonicalYieldCurve.as_of_date <= dates[-1],
            CanonicalYieldCurve.superseded_by.is_(None),
            CanonicalYieldCurve.withdrawn_at.is_(None),
            CanonicalYieldCurve.validation_status.in_(_INCLUDED_VALIDATION_STATUSES),
        )
        .group_by(CanonicalYieldCurve.source_system)
    ).tuples()
    source_first_seen: dict[str, date] = {
        source_system: first_seen for source_system, first_seen in source_rows
    }
    return {
        as_of: resolve_source_systems(
            source,
            {
                source_system
                for source_system, first_seen in source_first_seen.items()
                if first_seen <= as_of
            },
        )
        for as_of in dates
    }


def _winning_curve_ids(
    db: Session,
    organization_id: str,
    bank_id: str,
    currency: str,
    selected_sources_by_date: dict[date, tuple[str, ...]],
) -> set[UUID]:
    discount_name = market_data.desk_discount_curve_name(currency)
    projection_name = desk_projection_curve_name(currency)
    selectors: set[tuple[date, tuple[str, ...] | None, str | None, str | None]] = set()
    for as_of, selected_sources in selected_sources_by_date.items():
        source_scopes: tuple[tuple[str, ...] | None, ...] = (
            (selected_sources, None) if selected_sources else (None,)
        )
        for source_systems in source_scopes:
            selectors.update(
                {
                    (as_of, source_systems, projection_name, None),
                    (as_of, source_systems, None, None),
                    (as_of, source_systems, discount_name, None),
                    (as_of, source_systems, None, market_data.DISCOUNT_CURVE_TYPE),
                }
            )

    winner_queries = []
    for as_of, source_systems, curve_name, curve_type in selectors:
        winner = (
            select(CanonicalYieldCurve.id.label("curve_id"))
            .where(
                CanonicalYieldCurve.organization_id == organization_id,
                CanonicalYieldCurve.bank_id == bank_id,
                CanonicalYieldCurve.currency == currency,
                CanonicalYieldCurve.as_of_date <= as_of,
                CanonicalYieldCurve.superseded_by.is_(None),
                CanonicalYieldCurve.withdrawn_at.is_(None),
                CanonicalYieldCurve.validation_status.in_(_INCLUDED_VALIDATION_STATUSES),
            )
            .order_by(
                CanonicalYieldCurve.as_of_date.desc(),
                CanonicalYieldCurve.ingested_at.desc(),
                CanonicalYieldCurve.id.desc(),
            )
            .limit(1)
        )
        if source_systems is not None:
            winner = winner.where(CanonicalYieldCurve.source_system.in_(source_systems))
        if curve_name is not None:
            winner = winner.where(CanonicalYieldCurve.curve_name == curve_name)
        if curve_type is not None:
            winner = winner.where(CanonicalYieldCurve.curve_type == curve_type)
        winner_subquery = winner.subquery()
        winner_queries.append(select(winner_subquery.c.curve_id))
    return set(db.scalars(union_all(*winner_queries)))


def prefetch_preferred_curves(  # noqa: PLR0913 - one bounded request scope
    db: Session,
    organization_id: str,
    bank_id: str,
    currency: str,
    as_of_dates: list[date],
    *,
    now: Any | None = None,
) -> PrefetchedPreferredCurves:
    """Resolve preferred projection and discount curves with bounded queries.

    This is the request-scoped companion to the two preferred-curve resolvers.
    It preserves source-plane preference, graceful fallback, curve precedence,
    point ordering, and overlay composition while loading each participating
    table once instead of repeating the same absence/selection probes per trend
    point.
    """
    dates = sorted(set(as_of_dates))
    if not dates:
        return PrefetchedPreferredCurves(projection={}, discount={})
    choice = load_preference(db, organization_id, bank_id).curves
    currency = currency.upper()
    selected_sources_by_date = _curve_sources_by_date(
        db,
        organization_id,
        bank_id,
        choice.source,
        dates,
    )
    discount_name = market_data.desk_discount_curve_name(currency)
    projection_name = desk_projection_curve_name(currency)
    curve_ids = _winning_curve_ids(
        db,
        organization_id,
        bank_id,
        currency,
        selected_sources_by_date,
    )
    if not curve_ids:
        empty = dict.fromkeys(dates)
        return PrefetchedPreferredCurves(projection=empty.copy(), discount=empty)
    curves = list(
        db.scalars(
            select(CanonicalYieldCurve)
            .where(
                CanonicalYieldCurve.organization_id == organization_id,
                CanonicalYieldCurve.bank_id == bank_id,
                CanonicalYieldCurve.id.in_(curve_ids),
            )
            .order_by(
                CanonicalYieldCurve.as_of_date.desc(),
                CanonicalYieldCurve.ingested_at.desc(),
                CanonicalYieldCurve.id.desc(),
            )
        )
    )
    point_rows = db.execute(
        select(
            CanonicalYieldCurvePoint.yield_curve_id,
            CanonicalYieldCurvePoint.tenor_months,
            CanonicalYieldCurvePoint.rate,
        )
        .where(
            CanonicalYieldCurvePoint.organization_id == organization_id,
            CanonicalYieldCurvePoint.yield_curve_id.in_(curve_ids),
            CanonicalYieldCurvePoint.superseded_by.is_(None),
            CanonicalYieldCurvePoint.withdrawn_at.is_(None),
            CanonicalYieldCurvePoint.validation_status.in_(_INCLUDED_VALIDATION_STATUSES),
        )
        .order_by(
            CanonicalYieldCurvePoint.yield_curve_id,
            CanonicalYieldCurvePoint.tenor_months,
        )
    ).all()
    points_by_curve: dict[UUID, list[tuple[int, Decimal]]] = {}
    for curve_id, tenor, rate in point_rows:
        points_by_curve.setdefault(curve_id, []).append((int(tenor), Decimal(rate)))

    overlays: list[MarketDataOverlay] = []
    if choice.overlay:
        curve_names = {curve.curve_name for curve in curves}
        overlays = list(
            db.scalars(
                select(MarketDataOverlay)
                .where(
                    MarketDataOverlay.organization_id == organization_id,
                    MarketDataOverlay.bank_id == bank_id,
                    MarketDataOverlay.base_ref_kind == "curve",
                    MarketDataOverlay.base_curve_name.in_(curve_names),
                    MarketDataOverlay.superseded_by.is_(None),
                    MarketDataOverlay.effective_from <= dates[-1],
                    or_(
                        MarketDataOverlay.effective_to.is_(None),
                        MarketDataOverlay.effective_to >= dates[0],
                    ),
                )
                .order_by(MarketDataOverlay.created_at)
            )
        )
    resolved_now = now or market_data.utc_now()

    def get_view(
        as_of: date,
        *,
        source_systems: tuple[str, ...] | None,
        curve_name: str | None = None,
        curve_type: str | None = None,
    ) -> CurveView | None:
        selected = next(
            (
                curve
                for curve in curves
                if curve.as_of_date <= as_of
                and curve.currency == currency.upper()
                and (curve_name is None or curve.curve_name == curve_name)
                and (curve_type is None or curve.curve_type == curve_type)
                and (source_systems is None or curve.source_system in source_systems)
            ),
            None,
        )
        if selected is None:
            return None
        raw_points = tuple(points_by_curve.get(selected.id, ()))
        if not raw_points:
            return None
        resolved_points = raw_points
        if choice.overlay:
            active = [
                overlay
                for overlay in overlays
                if overlay.base_curve_name == selected.curve_name
                and market_data_overlays.is_active(overlay, as_of)
            ]
            composed = market_data_overlays.compose_curve(resolved_points, active)
            if composed is not None:
                resolved_points = composed.adjusted_points
        return CurveView(
            currency=selected.currency,
            curve_name=selected.curve_name,
            curve_type=selected.curve_type,
            as_of_date=selected.as_of_date,
            points=resolved_points,
            attribution=market_data._attribution(  # pyright: ignore[reportPrivateUsage]
                selected.ingested_at,
                selected.ingestion_batch_id,
                selected.source_system,
                market_data.ScopeCategory.YIELD_CURVE,
                resolved_now,
            ),
        )

    def discount_view(as_of: date, source_systems: tuple[str, ...] | None) -> CurveView | None:
        return get_view(as_of, source_systems=source_systems, curve_name=discount_name) or get_view(
            as_of,
            source_systems=source_systems,
            curve_type=market_data.DISCOUNT_CURVE_TYPE,
        )

    def projection_view(as_of: date, source_systems: tuple[str, ...] | None) -> CurveView | None:
        return get_view(
            as_of, source_systems=source_systems, curve_name=projection_name
        ) or get_view(as_of, source_systems=source_systems)

    projection_result: dict[date, CurveView | None] = {}
    discount_result: dict[date, CurveView | None] = {}
    for as_of in dates:
        selected_sources = selected_sources_by_date[as_of]
        for result, resolver in (
            (projection_result, projection_view),
            (discount_result, discount_view),
        ):
            preferred = resolver(as_of, selected_sources) if selected_sources else None
            if preferred is not None:
                result[as_of] = preferred
                continue
            fallback = resolver(as_of, None)
            result[as_of] = (
                _flag_fallback(fallback, choice.source) if fallback is not None else None
            )
    return PrefetchedPreferredCurves(
        projection=projection_result,
        discount=discount_result,
    )


def preferred_fx_spot(  # noqa: PLR0913 - scope + tenant + as-of is the request key
    db: Session,
    organization_id: str,
    bank_id: str,
    base_currency: str,
    quote_currency: str,
    as_of: date,
    *,
    now: Any | None = None,
) -> FxRateView | None:
    """The FX spot the calculation path consumes, under the ``fx`` preference."""
    choice = load_preference(db, organization_id, bank_id).fx
    resolved = resolve_source_systems(
        choice.source, _present_sources(db, organization_id, bank_id, "fx", as_of)
    )
    if resolved:
        view = market_data.get_fx_spot(
            db,
            organization_id,
            bank_id,
            base_currency,
            quote_currency,
            as_of,
            source_systems=resolved,
            now=now,
        )
        if view is not None:
            return view
    view = market_data.get_fx_spot(
        db, organization_id, bank_id, base_currency, quote_currency, as_of, now=now
    )
    return _flag_fallback(view, choice.source) if view is not None else None


def preferred_fx_history(  # noqa: PLR0913 - scope + tenant + as-of is the request key
    db: Session,
    organization_id: str,
    bank_id: str,
    base_currency: str,
    quote_currency: str,
    as_of: date,
    limit: int = market_data.DEFAULT_FX_HISTORY_LIMIT,
) -> list[tuple[date, Decimal]]:
    """The FX return series the VaR window consumes, under the ``fx`` preference."""
    choice = load_preference(db, organization_id, bank_id).fx
    resolved = resolve_source_systems(
        choice.source, _present_sources(db, organization_id, bank_id, "fx", as_of)
    )
    if resolved:
        history = market_data.get_fx_spot_history(
            db,
            organization_id,
            bank_id,
            base_currency,
            quote_currency,
            as_of,
            limit,
            source_systems=resolved,
        )
        if history:
            return history
    return market_data.get_fx_spot_history(
        db, organization_id, bank_id, base_currency, quote_currency, as_of, limit
    )


# ---------------------------------------------------------------------------
# Plane discovery + side-by-side comparison (spec §4 /planes)
# ---------------------------------------------------------------------------

_CATEGORY_SOURCE_COLUMN = {
    "curves": CanonicalYieldCurve,
    "fx": CanonicalFxRate,
    "rates": CanonicalMarketIndex,
}


def _present_sources(
    db: Session, organization_id: str, bank_id: str, category: str, as_of: date
) -> set[str]:
    """Distinct ``source_system`` values servable for the category at ``as_of``."""
    model = _CATEGORY_SOURCE_COLUMN[category]
    rows = db.scalars(
        select(model.source_system)
        .where(
            model.organization_id == organization_id,
            model.bank_id == bank_id,
            model.as_of_date <= as_of,
            model.superseded_by.is_(None),
            model.withdrawn_at.is_(None),
            model.validation_status.in_(_INCLUDED_VALIDATION_STATUSES),
        )
        .distinct()
    ).all()
    return set(rows)


def resolve_planes(
    db: Session, ctx: TenantContext, bank_id: str, category: str, as_of: date
) -> PlanesRead:
    """The same scope resolved under each base plane, side by side (spec §4)."""
    if category not in CATEGORIES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unknown category {category!r}; expected one of {CATEGORIES}.",
        )
    category = cast("Category", category)
    _get_bank_or_404(db, ctx, bank_id)
    org = ctx.organization_id
    choice = load_preference(db, org, bank_id).choice(category)
    present = _present_sources(db, org, bank_id, category, as_of)

    planes: list[PlaneRead] = []
    for source in SOURCE_CHOICES:
        resolved = resolve_source_systems(source, present)
        items = _plane_items(db, org, bank_id, category, as_of, resolved, overlay=choice.overlay)
        planes.append(
            PlaneRead(
                source=source,
                available=bool(items),
                is_selected=source == choice.source,
                items=items,
                attribution=items[0].attribution if items else None,
            )
        )
    return PlanesRead(
        category=category,
        as_of=as_of,
        selected_source=choice.source,
        overlay_enabled=choice.overlay,
        planes=planes,
        overlay=_overlay_preview(db, org, bank_id, category, as_of, choice, present),
    )


def _plane_items(  # noqa: PLR0913 - the plane resolution key is scope + tenant + source set
    db: Session,
    organization_id: str,
    bank_id: str,
    category: str,
    as_of: date,
    resolved: tuple[str, ...],
    *,
    overlay: bool,
) -> list[PlaneItemRead]:
    if not resolved:  # a 'bank' plane with no bank-side sources present
        return []
    if category == "curves":
        return _curve_plane_items(db, organization_id, bank_id, as_of, resolved, overlay=overlay)
    if category == "fx":
        return _fx_plane_items(db, organization_id, bank_id, as_of, resolved)
    return _index_plane_items(db, organization_id, bank_id, as_of, resolved)


def _curve_plane_items(  # noqa: PLR0913 - scope + tenant + source set is the request key
    db: Session,
    organization_id: str,
    bank_id: str,
    as_of: date,
    resolved: tuple[str, ...],
    *,
    overlay: bool,
) -> list[PlaneItemRead]:
    items: list[PlaneItemRead] = []
    for view in market_data.list_yield_curves(
        db, organization_id, bank_id, as_of=as_of, source_systems=resolved
    ):
        adjusted: list[PlaneCurvePointRead] = []
        if overlay:
            composed = _compose(db, organization_id, bank_id, view.curve_name, as_of, view.points)
            if composed != view.points:
                adjusted = [PlaneCurvePointRead(tenor_months=t, rate=r) for t, r in composed]
        items.append(
            PlaneCurveItemRead(
                currency=view.currency,
                curve_name=view.curve_name,
                curve_type=view.curve_type,
                as_of_date=view.as_of_date,
                points=[PlaneCurvePointRead(tenor_months=t, rate=r) for t, r in view.points],
                adjusted_points=adjusted,
                attribution=_plane_attr(view.attribution),
            )
        )
    return items


def _fx_plane_items(
    db: Session, organization_id: str, bank_id: str, as_of: date, resolved: tuple[str, ...]
) -> list[PlaneItemRead]:
    items: list[PlaneItemRead] = []
    for base, quote in market_data.list_fx_pairs(db, organization_id, bank_id, as_of):
        spot = market_data.get_fx_spot(
            db, organization_id, bank_id, base, quote, as_of, source_systems=resolved
        )
        if spot is None:
            continue
        items.append(
            PlaneFxItemRead(
                base=spot.base_currency,
                quote=spot.quote_currency,
                rate=spot.rate,
                as_of_date=spot.as_of_date,
                attribution=_plane_attr(spot.attribution),
            )
        )
    return items


def _index_plane_items(
    db: Session, organization_id: str, bank_id: str, as_of: date, resolved: tuple[str, ...]
) -> list[PlaneItemRead]:
    items: list[PlaneItemRead] = []
    for index_code, scenario in market_data.list_index_scopes(db, organization_id, bank_id, as_of):
        view = market_data.get_index(
            db, organization_id, bank_id, index_code, as_of, scenario, source_systems=resolved
        )
        if view is None:
            continue
        items.append(
            PlaneIndexItemRead(
                index_code=view.index_code,
                scenario=view.scenario,
                value=view.value,
                horizon_months=view.horizon_months,
                as_of_date=view.as_of_date,
                attribution=_plane_attr(view.attribution),
            )
        )
    return items


def _overlay_preview(  # noqa: PLR0913 - preview key is scope + tenant + selected plane
    db: Session,
    organization_id: str,
    bank_id: str,
    category: str,
    as_of: date,
    choice: CategoryChoice,
    present: set[str],
) -> PlaneOverlayRead:
    """The overlay layer preview (curves only; other categories carry none)."""
    if category != "curves":
        return PlaneOverlayRead(available=False, delta_preview=[])
    overlays = market_data_overlays.active_curve_overlays(db, organization_id, bank_id, as_of)
    if not overlays:
        return PlaneOverlayRead(available=False, delta_preview=[])
    resolved = resolve_source_systems(choice.source, present)
    deltas: list[PlaneOverlayDeltaRead] = []
    if resolved:
        for view in market_data.list_yield_curves(
            db, organization_id, bank_id, as_of=as_of, source_systems=resolved
        ):
            composed = _compose(db, organization_id, bank_id, view.curve_name, as_of, view.points)
            base_by_tenor = dict(view.points)
            for tenor, adjusted in composed:
                base = base_by_tenor.get(tenor)
                if base is None or adjusted == base:
                    continue
                deltas.append(
                    PlaneOverlayDeltaRead(
                        curve_name=view.curve_name,
                        tenor_months=tenor,
                        base=base,
                        adjusted=adjusted,
                        delta=adjusted - base,
                    )
                )
    return PlaneOverlayRead(available=True, delta_preview=deltas)


def _compose(  # noqa: PLR0913 - scope + tenant + curve key is the request
    db: Session,
    organization_id: str,
    bank_id: str,
    curve_name: str,
    as_of: date,
    points: tuple[tuple[int, Decimal], ...],
) -> tuple[tuple[int, Decimal], ...]:
    overlays = market_data_overlays.active_curve_overlays(
        db, organization_id, bank_id, as_of, curve_name=curve_name
    )
    composed = market_data_overlays.compose_curve(points, overlays)
    return composed.adjusted_points if composed is not None else points


def _plane_attr(attribution: SourceAttribution) -> PlaneAttributionRead:
    return PlaneAttributionRead(
        source_system=attribution.source_system,
        ingestion_batch_id=attribution.ingestion_batch_id,
        ingested_at=attribution.ingested_at,
        stale=attribution.stale,
        age_seconds=attribution.age_seconds,
    )


# ---------------------------------------------------------------------------
# Published forward grid (spec §4 FC-5/G1)
# ---------------------------------------------------------------------------

_DF_QUANTUM = Decimal("1E-12")
_PCT = Decimal("100")
_MONTHS_PER_YEAR = Decimal("12")


def get_forward_grid(  # noqa: PLR0913 - tenant scope plus optional as-of/frequency selectors
    db: Session,
    ctx: TenantContext,
    bank_id: str,
    curve_name: str,
    as_of: date,
    *,
    frequency: str | None = None,
) -> ForwardGridRead:
    """The published forward grid for a desk curve, from its approved determination.

    Reads the APPROVED/published :class:`DeskDetermination` whose
    ``derived_values`` carries ``curve_name`` (built by ``curve_construction``).
    The determination stores per-bucket forward yields (``tenor_months`` +
    ``rate_pct``); Start/End dates and cumulative discount factors are
    reconstructed from those buckets (see ``_forward_grid_rows``). Calibrating
    pillars come from the determination's ``input_snapshot``.
    """
    _get_bank_or_404(db, ctx, bank_id)
    if curve_name.startswith("AEQ."):
        from app.services.market_desk import entitlements as desk_entitlements  # noqa: PLC0415

        datasets = desk_entitlements.active_datasets(db, ctx.organization_id, as_of=as_of)
        # FC-6d: both the name-family dataset gate and the curve definition's
        # distribution tier vs the org's active tier.
        if not desk_entitlements.curve_visible(db, curve_name, datasets, as_of=as_of):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No forward grid is available for this curve.",
            )
    determination = _latest_curve_determination(db, curve_name, as_of)
    if determination is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No published forward grid for this curve at the requested date.",
        )
    derived_values = determination.derived_values or {}
    spec = derived_values.get("curves", {}).get(curve_name, {})
    raw_points = spec.get("points") or []
    points = sorted((int(p["tenor_months"]), Decimal(str(p["rate_pct"]))) for p in raw_points)
    stored_grid = derived_values.get("forward_grids", {}).get(curve_name, {})
    assumptions = _forward_grid_assumptions(stored_grid)
    available_frequencies = _available_forward_grid_frequencies(stored_grid, assumptions)
    default_frequency = assumptions.curve_frequency if assumptions else "3M"
    selected_frequency = (frequency or default_frequency).upper()
    if selected_frequency not in available_frequencies:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "The requested published forward-grid frequency is not available for this curve."
            ),
        )
    rows = _stored_forward_grid_rows(_stored_frequency_grid(stored_grid, selected_frequency))
    if not rows:
        rows = _stored_forward_grid_rows(stored_grid)
    return ForwardGridRead(
        curve_name=curve_name,
        currency=_curve_currency(curve_name),
        as_of=determination.cob_date,
        methodology_ref=f"{determination.methodology_code} v{determination.methodology_version}",
        interpolation=(
            assumptions.interpolation_method
            if assumptions is not None
            else _curve_interpolation(db, curve_name, as_of)
        ),
        grid_is_authoritative=bool(rows),
        frequency=selected_frequency,
        available_frequencies=available_frequencies,
        assumptions=assumptions,
        rows=rows or _forward_grid_rows(points, determination.cob_date),
        pillars=_forward_grid_pillars(determination.input_snapshot),
    )


def _available_forward_grid_frequencies(
    stored_grid: object, assumptions: ForwardGridAssumptionsRead | None
) -> list[str]:
    if isinstance(stored_grid, dict) and isinstance(stored_grid.get("grids"), dict):
        frequencies = sorted(
            frequency.upper()
            for frequency, grid in stored_grid["grids"].items()
            if isinstance(frequency, str) and isinstance(grid, dict) and grid.get("rows")
        )
        if frequencies:
            return frequencies
    return [assumptions.curve_frequency.upper() if assumptions else "3M"]


def _stored_frequency_grid(stored_grid: object, frequency: str) -> object:
    if not isinstance(stored_grid, dict) or not isinstance(stored_grid.get("grids"), dict):
        return stored_grid
    for candidate, grid in stored_grid["grids"].items():
        if isinstance(candidate, str) and candidate.upper() == frequency:
            return grid
    return {}


def _stored_forward_grid_rows(value: object) -> list[ForwardGridRowRead]:
    if not isinstance(value, dict) or not isinstance(value.get("rows"), list):
        return []
    rows: list[ForwardGridRowRead] = []
    for row in value["rows"]:
        if not isinstance(row, dict):
            return []
        try:
            rows.append(
                ForwardGridRowRead(
                    start=date.fromisoformat(str(row["start"])),
                    end=date.fromisoformat(str(row["end"])),
                    discount_factor=str(row["discount_factor"]),
                    forward_yield=str(row["forward_yield"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            return []
    return rows


def _forward_grid_assumptions(value: object) -> ForwardGridAssumptionsRead | None:
    if not isinstance(value, dict) or not isinstance(value.get("definition"), dict):
        return None
    try:
        return ForwardGridAssumptionsRead.model_validate(value["definition"])
    except ValueError:
        return None


def _latest_curve_determination(
    db: Session, curve_name: str, as_of: date
) -> DeskDetermination | None:
    candidates = db.scalars(
        select(DeskDetermination)
        .where(
            DeskDetermination.status.in_(_PUBLISHED_DETERMINATION_STATUSES),
            DeskDetermination.cob_date <= as_of,
        )
        .order_by(DeskDetermination.cob_date.desc(), DeskDetermination.created_at.desc())
        .limit(200)
    ).all()
    for determination in candidates:
        derived_values = determination.derived_values or {}
        curves = derived_values.get("curves", {})
        spec = curves.get(curve_name)
        stored_grid = derived_values.get("forward_grids", {}).get(curve_name, {})
        if (spec and spec.get("points")) or (
            isinstance(stored_grid, dict) and stored_grid.get("rows")
        ):
            return determination
    return None


def _forward_grid_rows(
    points: list[tuple[int, Decimal]], cob_date: date
) -> list[ForwardGridRowRead]:
    """Reconstruct Start/End/period-DF/Yield rows from per-bucket forward yields.

    Each stored point is a forward bucket ending at ``tenor_months`` (the
    ``curve_construction`` grid); its start is the previous bucket's end.
    ``forward_yield`` is the stored percent as a decimal fraction; the returned
    discount factor is the *period* discount ratio ``DF(End)/DF(Start)``. This
    matches ``domain.curves.multicurve.forward_grid`` and is required for a
    basis conversion to re-express the forward yield correctly. Older
    determinations do not contain the exact calendar-adjusted construction grid,
    so dates and accrual fractions remain explicitly labelled reconstruction.
    """
    rows: list[ForwardGridRowRead] = []
    prev_month = 0
    for tenor_months, rate_pct in points:
        if tenor_months <= prev_month:
            continue
        forward = rate_pct / _PCT
        year_fraction = Decimal(tenor_months - prev_month) / _MONTHS_PER_YEAR
        period_discount = Decimal(1) / (Decimal(1) + forward * year_fraction)
        rows.append(
            ForwardGridRowRead(
                start=_add_months(cob_date, prev_month),
                end=_add_months(cob_date, tenor_months),
                discount_factor=_dec_str(period_discount.quantize(_DF_QUANTUM)),
                forward_yield=_dec_str(forward),
            )
        )
        prev_month = tenor_months
    return rows


def _forward_grid_pillars(input_snapshot: list[Any] | None) -> list[ForwardGridPillarRead]:
    pillars: list[ForwardGridPillarRead] = []
    for entry in input_snapshot or []:
        if isinstance(entry, dict) and {"instrument", "tenor", "quote"} <= set(entry):
            pillars.append(
                ForwardGridPillarRead(
                    tenor=str(entry["tenor"]),
                    instrument=str(entry["instrument"]),
                    quote=str(entry["quote"]),
                )
            )
    return pillars


def _curve_interpolation(db: Session, curve_name: str, as_of: date) -> str:
    from app.services.market_desk import curve_construction  # noqa: PLC0415

    definition = curve_construction.get_active_definition(db, curve_name, as_of)
    return definition.interpolation_method if definition is not None else ""


def _curve_currency(curve_name: str) -> str:
    # AEQ.GHS.SOV.FWD -> GHS (spec §8 grammar ISSUER.CURRENCY.SECTOR.TYPE).
    parts = curve_name.split(".")
    return parts[1] if len(parts) >= 2 and len(parts[1]) == 3 else ""  # noqa: PLR2004 - ISO length


def _add_months(anchor: date, months: int) -> date:
    index = anchor.month - 1 + months
    year = anchor.year + index // 12
    month = index % 12 + 1
    day = min(anchor.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _dec_str(value: Decimal) -> str:
    return format(value, "f")


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _get_bank_or_404(db: Session, ctx: TenantContext, bank_id: str) -> Bank:
    bank = db.scalar(
        select(Bank).where(Bank.id == bank_id, Bank.organization_id == ctx.organization_id)
    )
    if bank is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bank not found.")
    return bank
