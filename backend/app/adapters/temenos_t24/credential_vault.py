"""Encrypted credential vault for Temenos core-banking connections.

Mirrors the market-data ``EncryptedDbVault`` and REUSES its crypto free
functions and master key verbatim (zero market-data edits): AES-256-GCM over
the ``temenos_connections.credential_ciphertext`` column, keyed off
``CREDENTIAL_VAULT_MASTER_KEY``. The DB column only ever sees base64 ciphertext.

Unlike the market-data vault (keyed by org/bank/vendor), a bank may hold several
Temenos connections, so this vault operates directly on a connection ROW. It
stores the OFS service-user password / IRIS-and-Open-API client secrets / API
keys, and returns :class:`TemenosCredentials` for a single sign-on cycle — the
caller must discard the plaintext after use and must never persist or log it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from app.adapters.market_data.credential_manager import (
    CredentialVaultError,
    credential_fingerprint,
    decrypt_credential_envelope,
    derive_master_key,
    encrypt_credential_envelope,
)
from app.adapters.temenos_t24.auth import TemenosCredentials
from app.core.config import get_settings

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.orm import Session

    from app.models.temenos import TemenosConnection

# Re-exported so callers catch one error type regardless of which vault raised.
__all__ = ["CredentialVaultError", "TemenosCredentialVault", "build_core_vault_path"]


def build_core_vault_path(bank_id: UUID | str, mode: str) -> str:
    """Logical credential locator for a core-banking connection."""
    return f"vault://institutions/{bank_id}/core_credentials/{mode}/default"


class TemenosCredentialVault:
    """MVP credential vault backed by ``TemenosConnection`` rows."""

    def __init__(self, db: Session, master_key: str | None = None) -> None:
        key_material = (
            master_key
            if master_key is not None
            else get_settings().market_data.credential_vault_master_key
        )
        if not key_material:
            msg = (
                "CREDENTIAL_VAULT_MASTER_KEY is not configured; "
                "the Temenos credential vault cannot operate."
            )
            raise CredentialVaultError(msg)
        self._db = db
        self._key = derive_master_key(key_material)

    def store(
        self,
        connection: TemenosConnection,
        *,
        credentials: dict[str, Any],
        expires_at: datetime | None = None,
    ) -> str:
        """Encrypt credentials onto the connection row and set fingerprint/expiry."""
        vault_path = build_core_vault_path(connection.bank_id, connection.connection_mode)
        connection.credential_ciphertext = encrypt_credential_envelope(
            self._key,
            institution_id=str(connection.bank_id),
            vendor=connection.connection_mode,
            credentials=credentials,
            issued_at=datetime.now(UTC),
            expires_at=expires_at,
        )
        connection.credential_fingerprint = credential_fingerprint(credentials)
        connection.credential_expires_at = expires_at
        connection.vault_path = vault_path
        self._db.flush()
        return vault_path

    def retrieve(self, connection: TemenosConnection) -> TemenosCredentials:
        """Decrypt for one sign-on cycle. Discard the result afterwards; never
        persist or log the plaintext."""
        stored = connection.credential_ciphertext
        if not stored:
            msg = f"Connection {connection.id} holds no stored credential."
            raise CredentialVaultError(msg)
        envelope = decrypt_credential_envelope(self._key, stored)
        return TemenosCredentials.from_dict(dict(envelope.get("credentials", {})))

    def delete(self, connection: TemenosConnection) -> None:
        """Wipe the stored ciphertext (cryptographic deletion). The row is kept
        for audit — revocation never deletes canonical data already pulled."""
        connection.credential_ciphertext = None
        connection.credential_fingerprint = None
        connection.credential_expires_at = None
        self._db.flush()

    def fingerprint(self, credentials: dict[str, Any]) -> str:
        return credential_fingerprint(credentials)
