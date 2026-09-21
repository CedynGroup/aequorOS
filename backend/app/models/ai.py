"""Per-tenant AI consent and switches (M4a).

One row per organisation, deliberately MUTABLE: a kill-switch that could not be
flipped would not be a kill-switch. The history is the append-only
``audit_events`` trail, which records every change with its before/after state
and the reason given.

The CHECK is the substantive rule: a row cannot be ``enabled`` without a consent
version, a consenting user and a timestamp. Consent is not a UI step that a
direct SQL write could skip.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UuidV7PrimaryKeyMixin


class AiCommentarySettings(UuidV7PrimaryKeyMixin, TimestampMixin, Base):
    """What one organisation has consented to, and which surfaces it allows."""

    __tablename__ = "ai_commentary_settings"
    __table_args__ = (
        UniqueConstraint("organization_id", name="uq_ai_commentary_settings_org"),
        CheckConstraint(
            "NOT enabled OR (consent_version IS NOT NULL AND consented_by IS NOT NULL "
            "AND consented_at IS NOT NULL)",
            name="ck_ai_commentary_settings_consent",
        ),
    )

    organization_id: Mapped[str] = mapped_column(
        String(16), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=sql_text("false"), nullable=False
    )
    #: Subset of ``app.services.ai.features.AI_FEATURES``; validated in the
    #: service, because a tenant consents to a NAMED use, not to "AI".
    enabled_features: Mapped[list[Any]] = mapped_column(
        JSON, default=list, server_default=sql_text("'[]'"), nullable=False
    )
    #: The stricter mode: descriptors, never values. Defaults ON — a tenant that
    #: has not chosen has not chosen to send figures.
    descriptor_only: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=sql_text("true"), nullable=False
    )
    consent_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    consented_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    consented_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)


__all__ = ["AiCommentarySettings"]
