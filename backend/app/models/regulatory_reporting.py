"""Regulatory Reporting & Submission Hub tables (docs/regulatory_reporting.md §3).

Packages are immutable, versioned snapshots of a generated return for one
``(bank, return_code, reporting_date)``: regeneration mints a new version and
marks the prior current version ``superseded`` — it never mutates. A partial
unique index keeps exactly one non-superseded version per key. Artifacts,
approvals, and submission events are append-only children; channel configs
hold per-bank submission-channel settings with write-only encrypted
credentials (EncryptedDbVault pattern).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UuidV7PrimaryKeyMixin, utc_now

PACKAGE_STATUSES = (
    "draft",
    "generated",
    "validated",
    "pending_approval",
    "approved",
    "submitted",
    "acknowledged",
    "rejected",
    "superseded",
)
RETURN_FAMILIES = ("liquidity", "capital", "irrbb", "fx", "icaap_stress")
RETURN_FREQUENCIES = ("monthly", "quarterly", "semiannual", "annual")
ARTIFACT_KINDS = ("xlsx", "csv", "pdf")
SUBMISSION_CHANNELS = ("orass_sandbox", "email", "manual")
SUBMISSION_EVENTS = ("submitted", "status_poll", "acknowledged", "rejected")
APPROVAL_ACTIONS = ("requested", "approved", "rejected")


def _values(options: tuple[str, ...]) -> str:
    return ", ".join(f"'{option}'" for option in options)


class RegulatoryPackage(UuidV7PrimaryKeyMixin, TimestampMixin, Base):
    """One immutable generated-return snapshot version for a reporting date."""

    __tablename__ = "regulatory_packages"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({_values(PACKAGE_STATUSES)})",
            name="ck_regulatory_packages_status",
        ),
        CheckConstraint(
            f"return_family IN ({_values(RETURN_FAMILIES)})",
            name="ck_regulatory_packages_return_family",
        ),
        CheckConstraint(
            f"frequency IN ({_values(RETURN_FREQUENCIES)})",
            name="ck_regulatory_packages_frequency",
        ),
        CheckConstraint("version >= 1", name="ck_regulatory_packages_version"),
        ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["banks.id", "banks.organization_id"],
        ),
        ForeignKeyConstraint(
            ["supersedes_id", "organization_id"],
            ["regulatory_packages.id", "regulatory_packages.organization_id"],
        ),
        UniqueConstraint("id", "organization_id", name="uq_regulatory_packages_id_org"),
        Index(
            "ix_regulatory_packages_org_bank_reporting_date",
            "organization_id",
            "bank_id",
            "reporting_date",
        ),
        Index(
            "ix_regulatory_packages_org_bank_status",
            "organization_id",
            "bank_id",
            "status",
        ),
        # One current (non-superseded) version per return per reporting date.
        Index(
            "uq_regulatory_packages_current",
            "organization_id",
            "bank_id",
            "return_code",
            "reporting_date",
            unique=True,
            postgresql_where=sql_text("status != 'superseded'"),
            sqlite_where=sql_text("status != 'superseded'"),
        ),
    )

    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    bank_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    return_family: Mapped[str] = mapped_column(String(20), nullable=False)
    return_code: Mapped[str] = mapped_column(String(40), nullable=False)
    reporting_date: Mapped[date] = mapped_column(Date, nullable=False)
    frequency: Mapped[str] = mapped_column(String(12), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    supersedes_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, server_default=sql_text("'{}'"), nullable=False
    )
    source_runs: Mapped[list[Any]] = mapped_column(
        JSON, default=list, server_default=sql_text("'[]'"), nullable=False
    )
    validation_report: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    generated_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class RegulatoryPackageArtifact(UuidV7PrimaryKeyMixin, Base):
    """One exported file (xlsx/csv/pdf) minted from a package snapshot."""

    __tablename__ = "regulatory_package_artifacts"
    __table_args__ = (
        CheckConstraint(
            f"kind IN ({_values(ARTIFACT_KINDS)})",
            name="ck_regulatory_package_artifacts_kind",
        ),
        ForeignKeyConstraint(
            ["package_id", "organization_id"],
            ["regulatory_packages.id", "regulatory_packages.organization_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("id", "organization_id", name="uq_regulatory_package_artifacts_id_org"),
        Index(
            "ix_regulatory_package_artifacts_org_package",
            "organization_id",
            "package_id",
        ),
    )

    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    package_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    object_path: Mapped[str] = mapped_column(String(512), nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class RegulatoryPackageApproval(UuidV7PrimaryKeyMixin, Base):
    """Append-only maker-checker trail (checker != maker enforced in service)."""

    __tablename__ = "regulatory_package_approvals"
    __table_args__ = (
        CheckConstraint(
            f"action IN ({_values(APPROVAL_ACTIONS)})",
            name="ck_regulatory_package_approvals_action",
        ),
        ForeignKeyConstraint(
            ["package_id", "organization_id"],
            ["regulatory_packages.id", "regulatory_packages.organization_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("id", "organization_id", name="uq_regulatory_package_approvals_id_org"),
        Index(
            "ix_regulatory_package_approvals_org_package",
            "organization_id",
            "package_id",
        ),
    )

    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    package_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    action: Mapped[str] = mapped_column(String(12), nullable=False)
    actor_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class RegulatorySubmissionEvent(UuidV7PrimaryKeyMixin, Base):
    """Append-only channel interaction log for a package."""

    __tablename__ = "regulatory_submission_events"
    __table_args__ = (
        CheckConstraint(
            f"channel IN ({_values(SUBMISSION_CHANNELS)})",
            name="ck_regulatory_submission_events_channel",
        ),
        CheckConstraint(
            f"event IN ({_values(SUBMISSION_EVENTS)})",
            name="ck_regulatory_submission_events_event",
        ),
        ForeignKeyConstraint(
            ["package_id", "organization_id"],
            ["regulatory_packages.id", "regulatory_packages.organization_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("id", "organization_id", name="uq_regulatory_submission_events_id_org"),
        Index(
            "ix_regulatory_submission_events_org_package",
            "organization_id",
            "package_id",
        ),
    )

    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    package_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    event: Mapped[str] = mapped_column(String(16), nullable=False)
    external_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    detail: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, server_default=sql_text("'{}'"), nullable=False
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class RegulatoryChannelConfig(UuidV7PrimaryKeyMixin, TimestampMixin, Base):
    """Per-bank submission-channel settings; credential material is write-only."""

    __tablename__ = "regulatory_channel_configs"
    __table_args__ = (
        CheckConstraint(
            f"channel IN ({_values(SUBMISSION_CHANNELS)})",
            name="ck_regulatory_channel_configs_channel",
        ),
        ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["banks.id", "banks.organization_id"],
        ),
        UniqueConstraint("id", "organization_id", name="uq_regulatory_channel_configs_id_org"),
        UniqueConstraint(
            "organization_id",
            "bank_id",
            "channel",
            name="uq_regulatory_channel_configs_scope",
        ),
        Index(
            "ix_regulatory_channel_configs_org_bank",
            "organization_id",
            "bank_id",
        ),
    )

    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    bank_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, server_default=sql_text("'{}'"), nullable=False
    )
    credential_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    credential_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
