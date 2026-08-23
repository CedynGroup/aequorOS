"""Server-side refresh-token state: rotation, reuse detection, revocation.

A refresh token is a 14-day bearer credential, and a stateless verifier can only
ever check its signature and expiry — so until this table existed there was no
way to end a session early. Deactivating the account was the only kill switch,
which meant a password rotation after a suspected compromise left the attacker's
refresh token working for up to a fortnight (audit finding P0-5).

Every issued refresh token now gets a row here, keyed by the ``jti`` it carries
(``id`` IS the ``jti``), and :mod:`app.services.authentication` consults it on
every refresh. What is stored is a SHA-256 digest of the token, never the token:
a database reader learns nothing they could present.

``family_id`` is the session lineage. A password login (or SSO sign-in) starts a
family; each rotation adds a member and retires its parent. Presenting a retired
member is either a benign concurrent retry (the grace window,
``AuthSettings.refresh_rotation_grace_seconds``) or theft — and theft revokes the
entire family, so a stolen token cannot outlive detection by the two parties both
holding descendants of the same login.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UuidV4PrimaryKeyMixin

#: Why a token stopped being usable. ``reuse_detected`` is the security event:
#: a token that had already been rotated was presented outside the grace window,
#: so every live member of its family was revoked with this reason.
REVOCATION_REASONS: tuple[str, ...] = (
    "logout",
    "password_change",
    "user_deactivated",
    "reuse_detected",
    "admin_revoked",
)


def _values(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


class RefreshToken(UuidV4PrimaryKeyMixin, TimestampMixin, Base):
    """One issued refresh token. ``id`` is the token's ``jti`` claim."""

    __tablename__ = "refresh_tokens"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_refresh_tokens_token_hash"),
        CheckConstraint(
            f"revoked_reason IS NULL OR revoked_reason IN ({_values(REVOCATION_REASONS)})",
            name="ck_refresh_tokens_revoked_reason",
        ),
        Index("ix_refresh_tokens_family_id", "family_id"),
        Index("ix_refresh_tokens_user_id", "user_id"),
        Index("ix_refresh_tokens_organization_id", "organization_id"),
        # Supports purging spent rows; the table is otherwise append-mostly.
        Index("ix_refresh_tokens_expires_at", "expires_at"),
    )

    organization_id: Mapped[str] = mapped_column(
        String(16),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: The session lineage. The first token of a login is its own family root.
    family_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    #: SHA-256 hex of the raw token — binds this row to the exact token bytes, so
    #: knowing a ``jti`` is not enough to refresh with or revoke a session.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: When this token was consumed by a refresh. Set once and never moved
    #: forward: the grace window is measured from the FIRST rotation, so
    #: re-presenting the same spent token cannot slide the window along.
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: The ``jti`` this token rotated into (the first successor, if the grace
    #: window produced siblings).
    replaced_by_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
