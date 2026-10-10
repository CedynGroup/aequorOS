"""Control-plane key references and opaque, bank-scoped object-key envelopes.

These tables contain configuration and wrapped keys, never financial values
or plaintext keys. The key-reference registry is resolved before the tenant is known; the
wrapped-key table enforces tenant RLS and explicit bank scoping at every lookup.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    LargeBinary,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TableArgs, TimestampMixin, UuidV4PrimaryKeyMixin, utc_now


class BankEncryptionKey(UuidV4PrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "bank_encryption_keys"
    __table_args__: TableArgs = (
        UniqueConstraint("bank_id", "organization_id", name="uq_bank_encryption_keys_bank_org"),
        ForeignKeyConstraint(["bank_id", "organization_id"], ["banks.id", "banks.organization_id"]),
        CheckConstraint(
            "provider IN ('aws_kms', 'local_test')", name="ck_bank_encryption_keys_provider"
        ),
        CheckConstraint(
            "status IN ('active', 'disabled', 'unavailable')", name="ck_bank_encryption_keys_status"
        ),
    )

    bank_id: Mapped[str] = mapped_column(String(16), unique=True, nullable=False)
    organization_id: Mapped[str] = mapped_column(
        String(16), ForeignKey("organizations.id"), nullable=False
    )
    storage_slug: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    key_id: Mapped[str] = mapped_column(String(2048), nullable=False)
    region: Mapped[str] = mapped_column(String(64), nullable=False)
    owner_account: Mapped[str] = mapped_column(String(12), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ObjectKeyEnvelope(UuidV4PrimaryKeyMixin, Base):
    __tablename__ = "object_key_envelopes"
    __table_args__: TableArgs = (
        ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["bank_encryption_keys.bank_id", "bank_encryption_keys.organization_id"],
        ),
    )

    bank_id: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    organization_id: Mapped[str] = mapped_column(String(16), nullable=False)
    # Storage context is authenticated inside both SDK messages. This opaque
    # digest avoids copying customer filenames into the control-plane table.
    context_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    wrapped_key: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
