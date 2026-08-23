"""Market-data entitlement tiers (curve platform spec §10).

Per-organization grants on desk-published dataset codes, effective-dated.
Analogous to LSEG DACS permissioning: not every tenant necessarily receives
every published product (tiering / licensing). Desk tables remain global;
entitlements gate **distribution** at publish and **visibility** at bank read.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UuidV7PrimaryKeyMixin

# Catalog of desk-published datasets (machine ids).
DESK_DATASET_CODES: tuple[str, ...] = (
    "DESK_RATES",  # policy, money-market, GRR, lending indicators
    "DESK_FX",  # interbank FX / reference rate
    "DESK_APR",  # competitor APR (public BoG)
    "DESK_CURVES_SOVEREIGN",  # AEQ.*.SOV.ZERO / FWD
    "DESK_CURVES_DISCOUNT",  # AEQ.*.OIS (AGD / true OIS)
    "DESK_CURVES_CREDIT",  # AEQ.*.CORP (Stage 3)
)

# Named tiers expand to dataset sets (grant can use either a tier or a dataset).
ENTITLEMENT_TIERS: dict[str, tuple[str, ...]] = {
    "core": ("DESK_RATES", "DESK_FX"),
    "standard": (
        "DESK_RATES",
        "DESK_FX",
        "DESK_APR",
        "DESK_CURVES_SOVEREIGN",
        "DESK_CURVES_DISCOUNT",
    ),
    "premium": (
        "DESK_RATES",
        "DESK_FX",
        "DESK_APR",
        "DESK_CURVES_SOVEREIGN",
        "DESK_CURVES_DISCOUNT",
        "DESK_CURVES_CREDIT",
    ),
}

ENTITLEMENT_STATUSES: tuple[str, ...] = ("active", "revoked")


def _values(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


class MarketDataEntitlement(UuidV7PrimaryKeyMixin, TimestampMixin, Base):
    """One effective-dated grant of one dataset (or expanded tier row) to an org.

    TENANT data, and FORCE row-level secured since migration ``202608230036``:
    a grant is a commercial fact about one bank, and the read path
    (``market_desk.entitlements.active_datasets``) grandfathers an org with no
    VISIBLE rows to the standard tier, so a leak in either direction changes
    what a tenant can see. Operators manage grants across tenants on the
    cross-tenant BYPASSRLS session (gated on a Tenant Inspector session and
    audited — ``app/operator/features/desk.py``); tenant sessions never write
    here. Every read filters by ``organization_id`` explicitly, RLS or not.
    """

    __tablename__ = "market_data_entitlements"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({_values(ENTITLEMENT_STATUSES)})",
            name="ck_market_data_entitlements_status",
        ),
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_market_data_entitlements_window",
        ),
        Index(
            "ix_market_data_entitlements_org_dataset",
            "organization_id",
            "dataset_code",
        ),
        UniqueConstraint(
            "organization_id",
            "dataset_code",
            "effective_from",
            name="uq_market_data_entitlements_org_dataset_from",
        ),
    )

    organization_id: Mapped[str] = mapped_column(
        String(20), ForeignKey("organizations.id"), nullable=False
    )
    dataset_code: Mapped[str] = mapped_column(String(40), nullable=False)
    # Optional label of the tier that produced this grant (core/standard/premium).
    tier: Mapped[str | None] = mapped_column(String(20), nullable=True)
    status: Mapped[str] = mapped_column(String(12), default="active", nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    granted_by: Mapped[str] = mapped_column(String(320), nullable=False)
    revoked_by: Mapped[str | None] = mapped_column(String(320), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
