"""SSO connection configuration: AequorOS' own OIDC relying-party settings.

One row per organization (``sso_connections``): the bank's IdP issuer, client id,
and a client secret sealed with the same AES-256-GCM credential envelope the
market-data vault uses (``CREDENTIAL_VAULT_MASTER_KEY``). Reads through the API
are write-only for the secret — responses carry only ``client_secret_set``. The
single plaintext read path is :func:`resolve_client_config`, which serves the
dashboard's server-to-server NextAuth config fetch (gated by ``SSO_INTERNAL_KEY``
at the route layer) and the token-verification lookup in
:mod:`app.services.authentication`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from fastapi import HTTPException, status
from sqlalchemy import select

from app.adapters.market_data.credential_manager import (
    CredentialVaultError,
    credential_fingerprint,
    decrypt_credential_envelope,
    derive_master_key,
    encrypt_credential_envelope,
)
from app.core.config import get_settings
from app.db.base import utc_now
from app.models import SsoConnection

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.orm import Session

_SECRET_KEY = "client_secret"


@dataclass(frozen=True)
class SsoClientConfig:
    """Transient decrypted OIDC client config — use for one exchange, then discard."""

    issuer: str
    client_id: str
    client_secret: str
    enabled: bool


def _master_key() -> bytes:
    key_material = get_settings().market_data.credential_vault_master_key
    if not key_material:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Credential vault is not configured (CREDENTIAL_VAULT_MASTER_KEY unset).",
        )
    return derive_master_key(key_material)


def normalize_domains(domains: list[str]) -> list[str]:
    """Lower-case, strip, dedupe (order-preserving), drop empties.

    A pasted email address ("cfo@bank.com") is a domain-list footgun — it would
    never match anything at sign-in — so anything containing '@' is reduced to
    the part after its last '@'.
    """
    seen: list[str] = []
    for raw in domains:
        domain = raw.strip().lower().rsplit("@", 1)[-1]
        if domain and domain not in seen:
            seen.append(domain)
    return seen


def get_connection(db: Session, organization_id: UUID) -> SsoConnection | None:
    return db.scalar(select(SsoConnection).where(SsoConnection.organization_id == organization_id))


def upsert_connection(  # noqa: PLR0913 - a connection is configured in one call
    db: Session,
    *,
    organization_id: UUID,
    issuer: str,
    client_id: str,
    client_secret: str | None,
    allowed_email_domains: list[str],
    enabled: bool,
    jit_enabled: bool = False,
    actor_user_id: UUID | None,
) -> SsoConnection:
    """Create or update the org's SSO connection. ``client_secret=None`` keeps
    the stored secret; a non-empty value replaces it (write-only semantics)."""
    issuer = issuer.strip().rstrip("/")
    if not issuer.startswith("https://"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Issuer must be an https:// URL exactly as the IdP publishes it.",
        )
    domains = normalize_domains(allowed_email_domains)
    if jit_enabled and not domains:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "Automatic account creation requires at least one allowed email "
                "domain — without it, any valid public account could sign up."
            ),
        )
    connection = get_connection(db, organization_id)
    if connection is None:
        connection = SsoConnection(organization_id=organization_id)
        db.add(connection)
    connection.issuer = issuer
    connection.client_id = client_id.strip()
    connection.allowed_email_domains = domains
    connection.jit_enabled = jit_enabled
    connection.updated_by = actor_user_id
    if client_secret:
        connection.client_secret_ciphertext = encrypt_credential_envelope(
            _master_key(),
            institution_id=str(organization_id),
            vendor="sso:oidc",
            credentials={_SECRET_KEY: client_secret},
            issued_at=utc_now(),
            expires_at=None,
        )
        connection.client_secret_fingerprint = credential_fingerprint({_SECRET_KEY: client_secret})
    if enabled and not connection.client_secret_ciphertext:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Cannot enable SSO before a client secret is set.",
        )
    connection.enabled = enabled
    db.commit()
    db.refresh(connection)
    return connection


def _open_secret(connection: SsoConnection) -> str:
    if not connection.client_secret_ciphertext:
        return ""
    try:
        envelope = decrypt_credential_envelope(_master_key(), connection.client_secret_ciphertext)
    except CredentialVaultError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Stored SSO client secret cannot be decrypted.",
        ) from exc
    return str(dict(envelope.get("credentials", {})).get(_SECRET_KEY, ""))


def find_enabled_by_issuer_audience(
    db: Session, *, issuer: str, audience: str
) -> SsoConnection | None:
    """Route an incoming id_token to its connection (cross-tenant system session)."""
    return db.scalar(
        select(SsoConnection).where(
            SsoConnection.enabled.is_(True),
            SsoConnection.issuer == issuer.rstrip("/"),
            SsoConnection.client_id == audience,
        )
    )


def resolve_client_config(db: Session) -> SsoClientConfig | None:
    """The single enabled connection's full client config, secret included.

    Phase 1 is one IdP per deployment: with several enabled connections the
    dashboard cannot know which to initiate, so refuse loudly rather than pick
    one (Phase 2's home-realm discovery lifts this).
    """
    connections = db.scalars(select(SsoConnection).where(SsoConnection.enabled.is_(True))).all()
    if not connections:
        return None
    if len(connections) > 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Multiple enabled SSO connections; per-org sign-in is not built yet.",
        )
    connection = connections[0]
    return SsoClientConfig(
        issuer=connection.issuer,
        client_id=connection.client_id,
        client_secret=_open_secret(connection),
        enabled=True,
    )
