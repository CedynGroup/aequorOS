from __future__ import annotations

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UuidPrimaryKeyMixin


class Organization(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
