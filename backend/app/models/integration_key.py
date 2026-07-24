from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UuidV4PrimaryKeyMixin


class IntegrationKey(UuidV4PrimaryKeyMixin, TimestampMixin, Base):
    """A revocable API key bound to a tenant service account.

    Bank middleware authenticates with the raw key as a bearer credential;
    only the SHA-256 hash is stored (the raw key is shown exactly once at
    issuance). DELIBERATELY NOT RLS-forced: authentication resolves the key
    hash BEFORE any tenant context exists, so the lookup must be global.
    The table carries no secret material (hashes + metadata only) and every
    read/mutation endpoint filters by the caller's organization explicitly.
    """

    __tablename__ = "integration_keys"
    __table_args__ = (
        Index("uq_integration_keys_key_hash", "key_hash", unique=True),
        Index("ix_integration_keys_organization_id", "organization_id"),
    )

    organization_id: Mapped[str] = mapped_column(
        String(16), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    # The machine identity requests act as (role-checked like any user).
    service_user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    label: Mapped[str] = mapped_column(String(80), nullable=False)
    # Display fragment ("aeq_live_AB12…") — never the full key.
    key_prefix: Mapped[str] = mapped_column(String(20), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
