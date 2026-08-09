"""Per-bank market data overlays: lifecycle + read-time curve composition.

The tenant-side half of the two-layer architecture
(AequorOS_Market_Data_and_Curve_Platform.md §2, §9): banks layer private,
effective-dated, component-tagged spread adjustments on the AequorOS golden
copy. This module owns the overlay lifecycle (create / append-only edit via
supersession / end) and the pure composition arithmetic the views feature
applies at read time. Golden canonical rows are never written here.

Composition rule (deterministic, order-independent):

    adjusted(tenor) = base(tenor) x product(multiplicative factors)
                      + sum(additive_bps) / 10_000
                      + sum(fixed rate fractions)

An overlay applies to a tenor when its ``tenor_months`` is NULL (flat) or
equals that tenor. Only current-generation rows (``superseded_by IS NULL``)
whose effective window covers the as-of date compose.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select

from app.db.base import utc_now
from app.models import Bank, MarketDataOverlay, User
from app.schemas.market_data_overlays import (
    MarketDataOverlayCreate,
    MarketDataOverlayEnd,
    MarketDataOverlayListRead,
    MarketDataOverlayRead,
)
from app.services.audit import record_event

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.api.deps import TenantContext

_BPS_PER_UNIT = Decimal(10_000)


# ---------------------------------------------------------------------------
# Composition (pure)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ComposedCurve:
    """Adjusted points plus the overlay rows that produced them."""

    adjusted_points: tuple[tuple[int, Decimal], ...]
    components: tuple[MarketDataOverlay, ...]


def is_active(overlay: MarketDataOverlay, as_of: date) -> bool:
    """Current generation + effective window covers ``as_of``."""
    if overlay.superseded_by is not None:
        return False
    if overlay.effective_from > as_of:
        return False
    return overlay.effective_to is None or overlay.effective_to >= as_of


def active_curve_overlays(
    db: Session,
    organization_id: str,
    bank_id: str,
    as_of: date,
    *,
    curve_name: str | None = None,
) -> list[MarketDataOverlay]:
    """Active curve overlays for the bank at ``as_of`` (optionally one curve)."""
    query = select(MarketDataOverlay).where(
        MarketDataOverlay.organization_id == organization_id,
        MarketDataOverlay.bank_id == bank_id,
        MarketDataOverlay.base_ref_kind == "curve",
        MarketDataOverlay.superseded_by.is_(None),
        MarketDataOverlay.effective_from <= as_of,
    )
    if curve_name is not None:
        query = query.where(MarketDataOverlay.base_curve_name == curve_name)
    rows = db.scalars(query.order_by(MarketDataOverlay.created_at)).all()
    return [row for row in rows if row.effective_to is None or row.effective_to >= as_of]


def compose_curve(
    points: tuple[tuple[int, Decimal], ...],
    overlays: list[MarketDataOverlay],
) -> ComposedCurve | None:
    """Apply active overlays to base ``(tenor, rate)`` points.

    Returns None when no overlay applies — the caller then omits the
    adjusted series entirely rather than duplicating the base.
    """
    if not overlays:
        return None
    adjusted: list[tuple[int, Decimal]] = []
    for tenor, base in points:
        applicable = [o for o in overlays if o.tenor_months is None or o.tenor_months == tenor]
        rate = base
        for overlay in applicable:
            if overlay.adjustment_type == "multiplicative":
                rate = rate * Decimal(overlay.value)
        for overlay in applicable:
            if overlay.adjustment_type == "additive_bps":
                rate += Decimal(overlay.value) / _BPS_PER_UNIT
            elif overlay.adjustment_type == "fixed":
                rate += Decimal(overlay.value)
        adjusted.append((tenor, rate))
    return ComposedCurve(adjusted_points=tuple(adjusted), components=tuple(overlays))


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def list_overlays(  # noqa: PLR0913 - tenant scope + read filters are the contract
    db: Session,
    ctx: TenantContext,
    bank_id: str,
    *,
    as_of: date,
    include_history: bool = False,
    base_curve_name: str | None = None,
) -> MarketDataOverlayListRead:
    _get_bank_or_404(db, ctx, bank_id)
    query = select(MarketDataOverlay).where(
        MarketDataOverlay.organization_id == ctx.organization_id,
        MarketDataOverlay.bank_id == bank_id,
    )
    if base_curve_name is not None:
        query = query.where(MarketDataOverlay.base_curve_name == base_curve_name)
    rows = list(db.scalars(query.order_by(MarketDataOverlay.created_at)))
    if not include_history:
        rows = [row for row in rows if is_active(row, as_of)]
    emails = _emails_by_user_id(db, ctx.organization_id, rows)
    overlays = [_read_model(row, as_of, emails) for row in rows]
    return MarketDataOverlayListRead(
        bank_id=bank_id, as_of_date=as_of, overlays=overlays, total=len(overlays)
    )


def create_overlay(
    db: Session,
    ctx: TenantContext,
    bank_id: str,
    payload: MarketDataOverlayCreate,
) -> MarketDataOverlayRead:
    _get_bank_or_404(db, ctx, bank_id)
    _validate_shape(payload)

    superseded: MarketDataOverlay | None = None
    if payload.supersedes is not None:
        superseded = _get_overlay_or_404(db, ctx, bank_id, payload.supersedes)
        if superseded.superseded_by is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="That overlay version has already been superseded.",
            )

    overlay = MarketDataOverlay(
        organization_id=ctx.organization_id,
        bank_id=bank_id,
        base_ref_kind=payload.base_ref_kind,
        base_curve_name=payload.base_curve_name,
        tenor_months=payload.tenor_months,
        adjustment_type=payload.adjustment_type,
        value=payload.value,
        component_tag=payload.component_tag,
        effective_from=payload.effective_from,
        effective_to=payload.effective_to,
        note=payload.note,
        created_by=ctx.actor_user_id,
    )
    db.add(overlay)
    db.flush()
    if superseded is not None:
        superseded.superseded_by = overlay.id

    record_event(
        db,
        ctx,
        event_type="market_data_overlay.created",
        entity_type="market_data_overlay",
        entity_id=overlay.id,
        details={
            "base_ref_kind": overlay.base_ref_kind,
            "base_curve_name": overlay.base_curve_name,
            "tenor_months": overlay.tenor_months,
            "adjustment_type": overlay.adjustment_type,
            "value": str(overlay.value),
            "component_tag": overlay.component_tag,
            "effective_from": overlay.effective_from.isoformat(),
            "effective_to": (overlay.effective_to.isoformat() if overlay.effective_to else None),
            "supersedes": str(payload.supersedes) if payload.supersedes else None,
        },
    )
    db.commit()
    emails = _emails_by_user_id(db, ctx.organization_id, [overlay])
    return _read_model(overlay, utc_now().date(), emails)


def end_overlay(
    db: Session,
    ctx: TenantContext,
    bank_id: str,
    overlay_id: UUID,
    payload: MarketDataOverlayEnd,
) -> MarketDataOverlayRead:
    _get_bank_or_404(db, ctx, bank_id)
    overlay = _get_overlay_or_404(db, ctx, bank_id, overlay_id)
    if overlay.superseded_by is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That overlay version has been superseded; end the current version.",
        )
    if payload.effective_to < overlay.effective_from:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="effective_to cannot precede the overlay's effective_from.",
        )
    overlay.effective_to = payload.effective_to
    record_event(
        db,
        ctx,
        event_type="market_data_overlay.ended",
        entity_type="market_data_overlay",
        entity_id=overlay.id,
        details={
            "base_ref_kind": overlay.base_ref_kind,
            "base_curve_name": overlay.base_curve_name,
            "component_tag": overlay.component_tag,
            "effective_to": payload.effective_to.isoformat(),
        },
    )
    db.commit()
    emails = _emails_by_user_id(db, ctx.organization_id, [overlay])
    return _read_model(overlay, utc_now().date(), emails)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _validate_shape(payload: MarketDataOverlayCreate) -> None:
    if (payload.base_ref_kind == "curve") != (payload.base_curve_name is not None):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A curve overlay must name base_curve_name; other kinds must not.",
        )
    if payload.tenor_months is not None and payload.base_ref_kind != "curve":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="tenor_months applies only to curve overlays.",
        )
    if payload.effective_to is not None and payload.effective_to < payload.effective_from:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="effective_to cannot precede effective_from.",
        )
    if payload.adjustment_type == "multiplicative" and payload.value <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A multiplicative overlay factor must be positive.",
        )


def _read_model(
    overlay: MarketDataOverlay,
    as_of: date,
    emails: dict[UUID, str],
) -> MarketDataOverlayRead:
    return MarketDataOverlayRead(
        id=overlay.id,
        bank_id=overlay.bank_id,
        base_ref_kind=overlay.base_ref_kind,
        base_curve_name=overlay.base_curve_name,
        tenor_months=overlay.tenor_months,
        adjustment_type=overlay.adjustment_type,
        value=Decimal(overlay.value),
        component_tag=overlay.component_tag,
        effective_from=overlay.effective_from,
        effective_to=overlay.effective_to,
        note=overlay.note,
        created_by=overlay.created_by,
        created_by_email=(
            emails.get(overlay.created_by) if overlay.created_by is not None else None
        ),
        superseded_by=overlay.superseded_by,
        active=is_active(overlay, as_of),
        created_at=overlay.created_at,
    )


def _emails_by_user_id(
    db: Session, organization_id: str, rows: list[MarketDataOverlay]
) -> dict[UUID, str]:
    user_ids = {row.created_by for row in rows if row.created_by is not None}
    if not user_ids:
        return {}
    pairs = db.execute(
        select(User.id, User.email).where(
            User.organization_id == organization_id, User.id.in_(user_ids)
        )
    ).all()
    return {user_id: email for user_id, email in pairs}


def _get_bank_or_404(db: Session, ctx: TenantContext, bank_id: str) -> Bank:
    bank = db.scalar(
        select(Bank).where(Bank.id == bank_id, Bank.organization_id == ctx.organization_id)
    )
    if bank is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bank not found.")
    return bank


def _get_overlay_or_404(
    db: Session, ctx: TenantContext, bank_id: str, overlay_id: UUID
) -> MarketDataOverlay:
    overlay = db.scalar(
        select(MarketDataOverlay).where(
            MarketDataOverlay.id == overlay_id,
            MarketDataOverlay.organization_id == ctx.organization_id,
            MarketDataOverlay.bank_id == bank_id,
        )
    )
    if overlay is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Overlay not found.")
    return overlay
