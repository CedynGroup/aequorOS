from __future__ import annotations

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin
from app.services.public_ids import new_organization_public_id


class Organization(TimestampMixin, Base):
    __tablename__ = "organizations"

    # THE tenant identifier (OR-XXXXXXXX): platform-generated at creation and
    # used everywhere (primary key, auth claims, RLS scoping, UI). One
    # identity, no aliases.
    id: Mapped[str] = mapped_column(
        String(16), primary_key=True, default=new_organization_public_id
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
