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
from app.models.market_desk_curves import DeskCurveDefinition

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

# Default when an org has zero entitlement rows: standard tier (grandfather).
DEFAULT_TIER = "standard"

# Ordered tier ladder (curve platform spec §10; core < standard < premium).
# FC-6d: a published curve's tier is its definition's tier, and an org sees it
# only when its own active tier is at or above the curve's tier.
TIER_RANK: dict[str, int] = {"core": 0, "standard": 1, "premium": 2}

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
    # Unknown AEQ curves require premium sovereign+ unless explicitly credit/ois;
    # non-AEQ vendor curves are not gated by desk entitlements at all.
    return not curve_code.startswith("AEQ.")


def org_tier(datasets: set[str]) -> str:
    """The highest named tier whose FULL dataset set the org holds (FC-6d).

    Tiers are nested dataset sets (core ⊂ standard ⊂ premium) and ``grant_tier``
    grants the full set, so a subset test is exact for a tier-granted org; a
    partial/ad-hoc grant that does not cover a whole tier resolves conservatively
    downward. An org with no rows resolves to the default (standard) because
    :func:`active_datasets` grandfathers it to the standard set.
    """
    tier = "core"
    for name in ("standard", "premium"):
        if set(ENTITLEMENT_TIERS[name]).issubset(datasets):
            tier = name
    return tier


def tier_allows(curve_tier: str, datasets: set[str]) -> bool:
    """True when the org's active tier is at or above the curve's tier (FC-6d)."""
    required = TIER_RANK.get(curve_tier, TIER_RANK[DEFAULT_TIER])
    return TIER_RANK.get(org_tier(datasets), 0) >= required


def curve_definition_tier(
    db: Session, curve_code: str, *, as_of: date | None = None
) -> str | None:
    """The entitlement tier governing a curve code, or ``None`` when ungoverned.

    Resolves the latest APPROVED :class:`DeskCurveDefinition` for the code
    (effective on ``as_of`` when given) and returns its ``entitlement_tier``.
    ``None`` means no governed definition owns the code (e.g. a synthetic AGD /
    OIS proxy published outside the construction workspace) — such curves keep
    only the name-family dataset gate, never a tier gate.
    """
    query = (
        select(DeskCurveDefinition)
        .where(
            DeskCurveDefinition.curve_code == curve_code,
            DeskCurveDefinition.status == "approved",
        )
        .order_by(DeskCurveDefinition.version.desc())
        .limit(1)
    )
    if as_of is not None:
        query = query.where(DeskCurveDefinition.effective_from <= as_of)
    row = db.scalar(query)
    return row.entitlement_tier if row is not None else None


def curve_visible(
    db: Session, curve_code: str, datasets: set[str], *, as_of: date | None = None
) -> bool:
    """Full read-time visibility of an AEQ.* curve for an org (FC-6d).

    Combines the existing name-family dataset gate (:func:`curve_allowed`) with
    the definition's distribution tier vs the org's active tier. A curve with no
    governed definition is gated by name only; a governed curve must ALSO be at
    or below the org's tier.
    """
    if not curve_allowed(curve_code, datasets):
        return False
    tier = curve_definition_tier(db, curve_code, as_of=as_of)
    if tier is None:
        return True
    return tier_allows(tier, datasets)


def index_allowed(index_code: str, datasets: set[str]) -> bool:
    """True when a granted dataset claims this index code family."""
    for dataset, prefixes in DATASET_INDEX_PREFIXES.items():
        if dataset not in datasets:
            continue
        if any(index_code.startswith(p) or index_code == p for p in prefixes):
            return True
    # An ungranted GHS.* index is a desk index the org does not hold; unknown
    # non-desk indices (vendor macros etc.) are not gated here.
    return not index_code.startswith("GHS.")


def grant_tier(  # noqa: PLR0913 - keyword-only grant record: every field is audited
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


def grant_dataset(  # noqa: PLR0913 - keyword-only grant record: every field is audited
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
    organization_id: str,
    revoked_by: str,
    effective_to: date | None = None,
) -> MarketDataEntitlement:
    """End-date one grant. ``organization_id`` is REQUIRED and is part of the
    lookup key, never a check applied afterwards.

    It used to be a bare ``db.get`` by id: any operator could revoke ANY
    tenant's market-data entitlement by guessing or enumerating a uuid, with
    nothing tying the call to the tenant it affected (audit finding D-26). A
    row belonging to another organization is indistinguishable here from one
    that does not exist — the same 404, so the id space stays unprobeable.
    """
    row = db.scalar(
        select(MarketDataEntitlement).where(
            MarketDataEntitlement.id == entitlement_id,
            MarketDataEntitlement.organization_id == organization_id,
        )
    )
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
