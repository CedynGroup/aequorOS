from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UuidV4PrimaryKeyMixin

# Kept in sync with app.core.security.ROLES.
USER_ROLES: tuple[str, ...] = ("admin", "approver", "analyst", "viewer")
# "oidc" covers every external IdP (Google Workspace, Entra, Okta, …) — the
# connection an identity came through lives in sso_connections, not here.
AUTH_PROVIDERS: tuple[str, ...] = ("password", "oidc")


def _values(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


class User(UuidV4PrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("organization_id", "email", name="uq_users_organization_id_email"),
        UniqueConstraint("id", "organization_id", name="uq_users_id_organization_id"),
        CheckConstraint(f"role IN ({_values(USER_ROLES)})", name="ck_users_role"),
        CheckConstraint(
            f"auth_provider IN ({_values(AUTH_PROVIDERS)})", name="ck_users_auth_provider"
        ),
        Index("ix_users_organization_id", "organization_id"),
        # SSO identity is unique per provider (an OAuth subject maps to one user).
        Index(
            "uq_users_auth_provider_sso_subject",
            "auth_provider",
            "sso_subject",
            unique=True,
            postgresql_where=sql_text("sso_subject IS NOT NULL"),
            sqlite_where=sql_text("sso_subject IS NOT NULL"),
        ),
    )

    organization_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Authorization: one role per user (see app.core.security.has_role hierarchy).
    role: Mapped[str] = mapped_column(
        String(16), default="viewer", server_default="viewer", nullable=False
    )
    # Credentials. password_hash is null for SSO-only users; sso_subject is the
    # OAuth provider's subject claim, null for password users.
    auth_provider: Mapped[str] = mapped_column(
        String(16), default="password", server_default="password", nullable=False
    )
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sso_subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Brute-force throttling.
    failed_login_attempts: Mapped[int] = mapped_column(
        Integer, default=0, server_default=sql_text("0"), nullable=False
    )
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
