"""Desk dataset entitlement resolution and grants (spec §10)."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException, status
from sqlalchemy import select

from app.adapters.market_data.scope_taxonomy import DataScope
from app.db.base import utc_now
from app.models.entitlements import (
    DESK_DATASET_CODES,
    ENTITLEMENT_TIERS,
    MarketDataEntitlement,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

# Default when an org has zero entitlement rows: standard tier (grandfather).
DEFAULT_TIER = "standard"

# Which publish scopes / curve families each dataset unlocks.
DATASET_SCOPES: dict[str, frozenset[DataScope]] = {
    "DESK_RATES": frozenset({DataScope.MACRO_GHANA_POLICY_RATE_PATH}),
    "DESK_FX": frozenset({DataScope.FX_SPOT_USD_GHS}),
    "DESK_APR": frozenset({DataScope.MACRO_GHANA_POLICY_RATE_PATH}),
    "DESK_CURVES_SOVEREIGN": frozenset({DataScope.YIELD_CURVE_GHS}),
    "DESK_CURVES_DISCOUNT": frozenset({DataScope.YIELD_CURVE_GHS}),
    "DESK_CURVES_CREDIT": frozenset({DataScope.YIELD_CURVE_GHS}),
}

# Curve name prefixes gated by dataset (within YIELD_CURVE_GHS scope).
DATASET_CURVE_PREFIXES: dict[str, tuple[str, ...]] = {
    "DESK_CURVES_SOVEREIGN": ("AEQ.", ".SOV."),
    "DESK_CURVES_DISCOUNT": (".OIS",),
    "DESK_CURVES_CREDIT": (".CORP",),
}

# Index / reference-rate code families gated by dataset.
DATASET_INDEX_PREFIXES: dict[str, tuple[str, ...]] = {
    "DESK_RATES": (
        "GHS.MPR",
        "GHS.INTERBANK",
        "GHS.TBILL",
        "GHS.BOGBILL",
        "GHS.GRR",
        "GHS.BASE.",
        "GHS.LENDING",
        "GHS.ECONDATA",
    ),
    "DESK_APR": ("GHS.APR.",),
    "DESK_FX": ("GHS.FX.", "GHS.USDGHS"),
}


def expand_tier(tier: str) -> tuple[str, ...]:
    datasets = ENTITLEMENT_TIERS.get(tier)
    if datasets is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unknown entitlement tier {tier!r}; choose {', '.join(ENTITLEMENT_TIERS)}.",
        )
    return datasets


def active_datasets(
    db: Session, organization_id: str, *, as_of: date | None = None
) -> set[str]:
    """Dataset codes the org may receive/view on ``as_of`` (default today)."""
    as_of = as_of or utc_now().date()
    rows = list(
        db.scalars(
            select(MarketDataEntitlement).where(
                MarketDataEntitlement.organization_id == organization_id,
                MarketDataEntitlement.status == "active",
                MarketDataEntitlement.effective_from <= as_of,
            )
        )
    )
    granted: set[str] = set()
    for row in rows:
        if row.effective_to is not None and row.effective_to < as_of:
            continue
        granted.add(row.dataset_code)
    if not granted:
        # Grandfather: no rows yet → standard package.
        return set(ENTITLEMENT_TIERS[DEFAULT_TIER])
    return granted


def scopes_for_datasets(datasets: set[str]) -> list[DataScope]:
    scopes: set[DataScope] = set()
    for code in datasets:
        scopes |= DATASET_SCOPES.get(code, frozenset())
    return sorted(scopes, key=lambda s: s.value)


def filter_scopes(
    scopes: list[DataScope], datasets: set[str]
) -> list[DataScope]:
    allowed = set(scopes_for_datasets(datasets))
    return [s for s in scopes if s in allowed]


def curve_allowed(curve_code: str, datasets: set[str]) -> bool:
    if "DESK_CURVES_CREDIT" in datasets and ".CORP" in curve_code:
        return True
    if "DESK_CURVES_DISCOUNT" in datasets and curve_code.endswith(".OIS"):
        return True
    if "DESK_CURVES_SOVEREIGN" in datasets and ".SOV." in curve_code:
        return True
    # Unknown AEQ curves require premium sovereign+ unless explicitly credit/ois.
    if curve_code.startswith("AEQ."):
        return False
    # Non-AEQ vendor curves are not gated by desk entitlements.
    return True


def index_allowed(index_code: str, datasets: set[str]) -> bool:
    """True when a granted dataset claims this index code family."""
    for dataset, prefixes in DATASET_INDEX_PREFIXES.items():
        if dataset not in datasets:
            continue
        if any(index_code.startswith(p) or index_code == p for p in prefixes):
            return True
    # Unknown non-desk indices (vendor macros etc.) are not gated here.
    if index_code.startswith("GHS."):
        return False
    return True


def grant_tier(
    db: Session,
    *,
    organization_id: str,
    tier: str,
    effective_from: date,
    granted_by: str,
    notes: str | None = None,
) -> list[MarketDataEntitlement]:
    """Expand a tier into per-dataset active grants (idempotent per dataset)."""
    datasets = expand_tier(tier)
    created: list[MarketDataEntitlement] = []
    for dataset_code in datasets:
        existing = db.scalar(
            select(MarketDataEntitlement).where(
                MarketDataEntitlement.organization_id == organization_id,
                MarketDataEntitlement.dataset_code == dataset_code,
                MarketDataEntitlement.status == "active",
                MarketDataEntitlement.effective_from == effective_from,
            )
        )
        if existing is not None:
            created.append(existing)
            continue
        row = MarketDataEntitlement(
            organization_id=organization_id,
            dataset_code=dataset_code,
            tier=tier,
            status="active",
            effective_from=effective_from,
            granted_by=granted_by,
            notes=notes,
        )
        db.add(row)
        created.append(row)
    db.flush()
    return created


def grant_dataset(
    db: Session,
    *,
    organization_id: str,
    dataset_code: str,
    effective_from: date,
    granted_by: str,
    notes: str | None = None,
    tier: str | None = None,
) -> MarketDataEntitlement:
    if dataset_code not in DESK_DATASET_CODES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"Unknown dataset_code {dataset_code!r}; "
                f"choose {', '.join(DESK_DATASET_CODES)}."
            ),
        )
    row = MarketDataEntitlement(
        organization_id=organization_id,
        dataset_code=dataset_code,
        tier=tier,
        status="active",
        effective_from=effective_from,
        granted_by=granted_by,
        notes=notes,
    )
    db.add(row)
    db.flush()
    return row


def revoke(
    db: Session,
    entitlement_id: Any,
    *,
    revoked_by: str,
    effective_to: date | None = None,
) -> MarketDataEntitlement:
    row = db.get(MarketDataEntitlement, entitlement_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Entitlement does not exist.",
        )
    if row.status == "revoked":
        return row
    row.status = "revoked"
    row.revoked_by = revoked_by
    row.revoked_at = utc_now()
    row.effective_to = effective_to or utc_now().date()
    db.flush()
    return row


def list_entitlements(
    db: Session,
    *,
    organization_id: str | None = None,
    include_revoked: bool = False,
) -> list[MarketDataEntitlement]:
    query = select(MarketDataEntitlement).order_by(
        MarketDataEntitlement.organization_id,
        MarketDataEntitlement.dataset_code,
        MarketDataEntitlement.effective_from.desc(),
    )
    if organization_id is not None:
        query = query.where(MarketDataEntitlement.organization_id == organization_id)
    if not include_revoked:
        query = query.where(MarketDataEntitlement.status == "active")
    return list(db.scalars(query))


def catalog() -> dict[str, Any]:
    return {
        "datasets": list(DESK_DATASET_CODES),
        "tiers": {name: list(codes) for name, codes in ENTITLEMENT_TIERS.items()},
        "default_tier": DEFAULT_TIER,
    }
