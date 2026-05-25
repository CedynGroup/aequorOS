from __future__ import annotations

from uuid import UUID

from sqlalchemy import Boolean, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UuidPrimaryKeyMixin


class User(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("organization_id", "email", name="uq_users_organization_id_email"),
        Index("ix_users_organization_id", "organization_id"),
    )

    organization_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
