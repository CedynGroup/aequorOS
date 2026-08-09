"""Operator control-plane tables (docs/internal/developer.md).

Deliberately NOT RLS-forced: these are control-plane records owned by the
operator role, not tenant data. ``operator_audit_log`` is append-only on
Postgres (same DB-trigger guard as ``audit_events``); ``tenant_storage`` is
the per-organization storage registry the provisioning saga writes (bucket
names, encryption key ARN, provider).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Index, String
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UuidV4PrimaryKeyMixin, utc_now

OPERATOR_AUTH_MODES: tuple[str, ...] = ("dev", "oidc")
TENANT_STORAGE_PROVIDERS: tuple[str, ...] = ("minio", "aws")


def _values(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


class OperatorAuditLog(UuidV4PrimaryKeyMixin, Base):
    """One operator action (who, what, against which tenant).

    Append-only: a Postgres trigger (migration 202608090042, the
    ``audit_events`` pattern) blocks UPDATE and DELETE, so the record of what
    AequorOS staff did to tenant state cannot be rewritten.
    """

    __tablename__ = "operator_audit_log"
    __table_args__ = (
        CheckConstraint(
            f"auth_mode IN ({_values(OPERATOR_AUTH_MODES)})",
            name="ck_operator_audit_log_auth_mode",
        ),
        Index("ix_operator_audit_log_created_at", "created_at"),
        Index("ix_operator_audit_log_target_org", "target_org"),
    )

    operator_email: Mapped[str] = mapped_column(String(320), nullable=False)
    auth_mode: Mapped[str] = mapped_column(String(8), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    # The tenant acted on (OR-XXXXXXXX); null for cross-tenant reads.
    target_org: Mapped[str | None] = mapped_column(String(16), nullable=True)
    detail: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, server_default=sql_text("'{}'"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class TenantStorage(UuidV4PrimaryKeyMixin, TimestampMixin, Base):
    """Per-organization storage provisioning registry (developer.md §2a).

    One row per tenant, written by the provisioning saga: the four tier
    bucket names, the per-tenant KMS key ARN when AWS provisioning applied
    SSE-KMS (the ARN is config, not a secret — access is IAM-governed), and
    which provider the buckets live on.
    """

    __tablename__ = "tenant_storage"
    __table_args__ = (
        CheckConstraint(
            f"provider IN ({_values(TENANT_STORAGE_PROVIDERS)})",
            name="ck_tenant_storage_provider",
        ),
    )

    # Unique => indexed; one storage registry row per tenant.
    organization_id: Mapped[str] = mapped_column(
        String(16),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    bucket_names: Mapped[list[str]] = mapped_column(
        JSON, default=list, server_default=sql_text("'[]'"), nullable=False
    )
    kms_key_arn: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    provisioned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
