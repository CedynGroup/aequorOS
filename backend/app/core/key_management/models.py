"""Control-plane key references and wrapped, bank-scoped data-key versions.

These tables contain configuration and wrapped keys, never financial values
or plaintext keys. The key-reference registry is resolved before the tenant is known; the
wrapped-key table enforces tenant RLS and explicit bank scoping at every lookup.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TableArgs, TimestampMixin, UuidV4PrimaryKeyMixin, utc_now


class BankEncryptionKey(UuidV4PrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "bank_encryption_keys"
    __table_args__: TableArgs = (
        UniqueConstraint("bank_id", "organization_id", name="uq_bank_encryption_keys_bank_org"),
        ForeignKeyConstraint(["bank_id", "organization_id"], ["banks.id", "banks.organization_id"]),
        ForeignKeyConstraint(
            ["current_version_id", "bank_id", "organization_id"],
            [
                "object_key_envelopes.id",
                "object_key_envelopes.bank_id",
                "object_key_envelopes.organization_id",
            ],
            name="fk_bank_current_version",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["index_version_id", "bank_id", "organization_id"],
            [
                "object_key_envelopes.id",
                "object_key_envelopes.bank_id",
                "object_key_envelopes.organization_id",
            ],
            name="fk_bank_index_version",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint("provider = 'aws_kms'", name="ck_bank_encryption_keys_provider"),
        CheckConstraint(
            "status IN ('active', 'disabled', 'unavailable')", name="ck_bank_encryption_keys_status"
        ),
    )

    current_version_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    index_version_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
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
    rotation_period_days: Mapped[int | None] = mapped_column(Integer)
    rotation_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_successful_use_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ObjectKeyEnvelope(UuidV4PrimaryKeyMixin, Base):
    __tablename__ = "object_key_envelopes"
    __table_args__: TableArgs = (
        UniqueConstraint("id", "bank_id", "organization_id", name="uq_object_envelope_scope"),
        ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["bank_encryption_keys.bank_id", "bank_encryption_keys.organization_id"],
        ),
    )

    bank_id: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    organization_id: Mapped[str] = mapped_column(String(16), nullable=False)
    context_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    wrapped_key: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    key_id: Mapped[str] = mapped_column(String(2048), nullable=False)
    region: Mapped[str] = mapped_column(String(64), nullable=False)
    owner_account: Mapped[str] = mapped_column(String(12), nullable=False)
    last_successful_use_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class RetainedBankKey(UuidV4PrimaryKeyMixin, Base):
    __tablename__ = "retained_bank_keys"
    __table_args__: TableArgs = (
        ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["bank_encryption_keys.bank_id", "bank_encryption_keys.organization_id"],
        ),
        UniqueConstraint("bank_id", "provider", "key_id", name="uq_retained_bank_keys_identity"),
        CheckConstraint("provider = 'aws_kms'", name="ck_retained_bank_keys_provider"),
    )

    bank_id: Mapped[str] = mapped_column(String(16), nullable=False)
    organization_id: Mapped[str] = mapped_column(String(16), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    key_id: Mapped[str] = mapped_column(String(2048), nullable=False, index=True)
    region: Mapped[str] = mapped_column(String(64), nullable=False)
    owner_account: Mapped[str] = mapped_column(String(12), nullable=False)
    rotated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decrypt_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
