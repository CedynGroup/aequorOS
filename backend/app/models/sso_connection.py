from __future__ import annotations

from uuid import UUID

from sqlalchemy import JSON, Boolean, ForeignKey, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UuidV4PrimaryKeyMixin


class SsoConnection(UuidV4PrimaryKeyMixin, TimestampMixin, Base):
    """Per-organization OIDC relying-party configuration (AequorOS' own SSO).

    One connection per organization for now (Phase 2 of docs/rbac.md lifts this
    to many connections + home-realm discovery). The IdP client secret is never
    stored in plaintext: it is sealed with the same AES-256-GCM envelope the
    market-data credential vault uses (``CREDENTIAL_VAULT_MASTER_KEY``), and the
    API only ever reports whether a secret is set — reads of the plaintext are
    reserved for the dashboard's internal server-to-server config fetch.
    """

    __tablename__ = "sso_connections"
    __table_args__ = (
        UniqueConstraint("organization_id", name="uq_sso_connections_organization_id"),
    )

    organization_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    # OIDC issuer exactly as the IdP reports it (e.g. https://accounts.google.com,
    # https://login.microsoftonline.com/{tenant}/v2.0). Discovery + the token's
    # `iss` claim are both matched against this string.
    issuer: Mapped[str] = mapped_column(String(512), nullable=False)
    client_id: Mapped[str] = mapped_column(String(255), nullable=False)
    client_secret_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    client_secret_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Lower-cased email domains allowed to sign in via this connection (empty =
    # any domain; the pre-provisioned-user gate still applies either way).
    allowed_email_domains: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # JIT provisioning: when on, a verified identity from an ALLOWED domain that
    # has no AequorOS account gets one auto-created as a read-only `viewer`.
    # Deliberately opt-in and refused unless allowed_email_domains is non-empty —
    # without the domain gate, any valid public-IdP account would mint a user.
    jit_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    updated_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
