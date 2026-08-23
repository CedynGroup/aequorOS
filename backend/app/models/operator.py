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

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UuidV4PrimaryKeyMixin, utc_now

OPERATOR_AUTH_MODES: tuple[str, ...] = ("dev", "oidc", "password")
#: Staff roles, least- to most-privileged: ``developer`` uses every read/desk
#: surface; ``operator_admin`` additionally manages operator accounts (at or
#: below its own rank); ``super_admin`` is the unrestricted founder tier —
#: everything, including managing other admins. Rank order is positional.
OPERATOR_ROLES: tuple[str, ...] = ("developer", "operator_admin", "super_admin")

#: Positional privilege rank — higher outranks lower. Management actions
#: require the actor's rank >= the target's rank (so only a super_admin can
#: touch super_admin or operator_admin rows' credentials/status).
OPERATOR_ROLE_RANK: dict[str, int] = {role: rank for rank, role in enumerate(OPERATOR_ROLES)}
TENANT_STORAGE_PROVIDERS: tuple[str, ...] = ("minio", "aws")
#: Tenant-inspector session modes. ``consent`` is the routine path (the tenant
#: asked for support); ``break_glass`` is the emergency, admin-gated path. Both
#: are READ-ONLY session tracking this wave — the row + audit + UI banner are the
#: control; no act-as-user token is ever minted.
OPERATOR_INSPECTOR_MODES: tuple[str, ...] = ("consent", "break_glass")


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


class OperatorUser(UuidV4PrimaryKeyMixin, TimestampMixin, Base):
    """One AequorOS staff account for the operator console.

    Mirrors the CLIENT identity model (tenant ``users``): email+password is
    the primary sign-in (Argon2id hash, same scheme as tenant accounts) and
    workforce OIDC is the secondary path — an OIDC sign-in must find an active
    row here and takes its role from that row.

    GLOBAL table, deliberately NOT RLS-forced (operator precedent: these are
    control-plane records owned by the operator role, not tenant data).
    ``email`` is unique and stored lowercase — the login path lowercases
    before lookup, and the create paths normalize before insert.
    ``password_hash`` is nullable: an OIDC-only staff account has no
    password, exactly like an SSO-only tenant user.
    """

    __tablename__ = "operator_users"
    __table_args__ = (
        CheckConstraint(
            f"role IN ({_values(OPERATOR_ROLES)})",
            name="ck_operator_users_role",
        ),
    )

    # Unique => indexed; the login lookup path.
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Brute-force throttling — the SAME two columns tenant ``users`` carry, read
    # and written by the SAME primitive (``app/services/auth_throttle.py``).
    # Until migration 202608230041 the staff plane had none: its only control
    # was a per-process ``(email, ip)`` dict, so rotating source addresses gave
    # an unbounded budget against the account that yields a cross-tenant
    # BYPASSRLS session (audit finding D-25). Durable state is the point — it
    # is shared by every worker and replica and survives a deploy.
    failed_login_attempts: Mapped[int] = mapped_column(
        Integer, default=0, server_default=sql_text("0"), nullable=False
    )
    locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
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


class OperatorInspectorSession(UuidV4PrimaryKeyMixin, Base):
    """One READ-ONLY tenant-inspector session (staff_UI.md tenant inspector).

    Append-only session TRACKING, not an access grant: opening a session mints
    NO tenant token and no act-as-user claim — the console renders tenant data
    through the operator read endpoints, and this row (plus its
    ``operator_audit_log`` entries and the UI banner) is the diligence control
    that says WHO looked at WHICH tenant, WHEN, WHY, and under which mode. True
    act-as-user sign-in is deliberately out of scope (separate security review),
    so ``read_only`` is always true this wave.

    GLOBAL table, deliberately NOT RLS-forced (the operator-control-plane
    precedent): these are staff records owned by the operator role.
    """

    __tablename__ = "operator_inspector_sessions"
    __table_args__ = (
        CheckConstraint(
            f"mode IN ({_values(OPERATOR_INSPECTOR_MODES)})",
            name="ck_operator_inspector_sessions_mode",
        ),
        Index(
            "ix_operator_inspector_sessions_org_started",
            "organization_id",
            "started_at",
        ),
        Index("ix_operator_inspector_sessions_started_at", "started_at"),
    )

    organization_id: Mapped[str] = mapped_column(
        String(16),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    started_by: Mapped[str] = mapped_column(String(320), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str] = mapped_column(String, nullable=False)
    read_only: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=sql_text("true"), nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_by: Mapped[str | None] = mapped_column(String(320), nullable=True)
