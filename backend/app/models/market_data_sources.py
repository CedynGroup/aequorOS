"""Per-bank market-data source preference (market_data_sources.md §2).

One RLS-forced row per bank recording, per category (``curves`` / ``fx`` /
``rates``), which base plane drives the risk engines
(``aequor`` / ``bank`` / ``vendor``) and whether the bank's private overlay
layer composes on top. The arbitration getters in
``app/services/market_data.py`` honour this selection so the choice flows
live into IRRBB/FTP (spec §3). Absent a row the synthesised default is
``aequor`` + overlay on for every category — the current published behaviour
(spec §2 Defaults).
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UuidV4PrimaryKeyMixin

# The three category base-source choices (spec §2). ``bank`` resolves to
# every non-aequor / non-vendor source_system present for the bank.
SOURCE_CHOICES: tuple[str, ...] = ("aequor", "bank", "vendor")


class MarketDataSourcePreference(UuidV4PrimaryKeyMixin, TimestampMixin, Base):
    """The bank's per-category market-data source selection (one row per bank)."""

    __tablename__ = "market_data_source_preferences"
    __table_args__ = (
        CheckConstraint(
            f"curves_source IN {SOURCE_CHOICES!r}",
            name="ck_market_data_source_preferences_curves_source",
        ),
        CheckConstraint(
            f"fx_source IN {SOURCE_CHOICES!r}",
            name="ck_market_data_source_preferences_fx_source",
        ),
        CheckConstraint(
            f"rates_source IN {SOURCE_CHOICES!r}",
            name="ck_market_data_source_preferences_rates_source",
        ),
        UniqueConstraint(
            "organization_id", "bank_id", name="uq_market_data_source_preferences_bank"
        ),
        Index("ix_market_data_source_preferences_bank", "organization_id", "bank_id"),
    )

    organization_id: Mapped[str] = mapped_column(
        String(16), ForeignKey("organizations.id"), nullable=False
    )
    bank_id: Mapped[str] = mapped_column(String(16), ForeignKey("banks.id"), nullable=False)

    curves_source: Mapped[str] = mapped_column(String(8), nullable=False, default="aequor")
    fx_source: Mapped[str] = mapped_column(String(8), nullable=False, default="aequor")
    rates_source: Mapped[str] = mapped_column(String(8), nullable=False, default="aequor")

    curves_overlay: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    fx_overlay: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    rates_overlay: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # The user who last wrote the preference (audit companion to updated_at).
    updated_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    # created_at / updated_at come from TimestampMixin.
