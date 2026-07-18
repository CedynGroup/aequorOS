"""Credential vault seam for direct core-database connections.

Reuses the market-data ``EncryptedDbVault`` crypto free functions and master key
verbatim (zero market-data edits): AES-256-GCM over a base64 ciphertext envelope,
keyed off ``CREDENTIAL_VAULT_MASTER_KEY``. The read-only service account's
password (plus any wallet/extra secret material) is sealed into an opaque
ciphertext string that a connection row stores, and retrieved for exactly one
pull cycle as a transient :class:`DbCredentials` the caller must discard.

Unlike the market-data and Temenos vaults, this seam operates on ciphertext
STRINGS rather than a specific ORM row: the persistence of the ciphertext (which
connection table, which column) is an integration concern wired single-threaded,
so the adapter package stays free of shared-registry edits. The only credential
representation that may ever reach a log is the SHA-256 fingerprint.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from app.adapters.database_direct.drivers.base import DbCredentials
from app.adapters.market_data.credential_manager import (
    CredentialVaultError,
    credential_fingerprint,
    decrypt_credential_envelope,
    derive_master_key,
    encrypt_credential_envelope,
)
from app.core.config import get_settings

if TYPE_CHECKING:
    from uuid import UUID

__all__ = [
    "CredentialVaultError",
    "DatabaseDirectCredentialVault",
    "build_db_vault_path",
]

# Envelope keys for the credential dict. Only ``username``/``password`` are
# required; ``extra`` carries backend-specific secret material (wallet password,
# ticket cache path) surfaced onto DbCredentials.extra.
_USERNAME_KEY = "username"
_PASSWORD_KEY = "password"
_EXTRA_KEY = "extra"


def build_db_vault_path(bank_id: UUID | str, backend: str) -> str:
    """Logical credential locator for a direct core-database connection."""
    return f"vault://institutions/{bank_id}/db_direct/{backend}/default"


class DatabaseDirectCredentialVault:
    """Seals and opens database-direct credential envelopes.

    ``seal`` returns opaque base64 ciphertext for a connection row to store;
    ``open`` decrypts it into a transient :class:`DbCredentials` for one pull.
    """

    def __init__(self, master_key: str | None = None) -> None:
        key_material = (
            master_key
            if master_key is not None
            else get_settings().market_data.credential_vault_master_key
        )
        if not key_material:
            msg = (
                "CREDENTIAL_VAULT_MASTER_KEY is not configured; "
                "the database-direct credential vault cannot operate."
            )
            raise CredentialVaultError(msg)
        self._key = derive_master_key(key_material)

    def seal(
        self,
        *,
        institution_id: str,
        backend: str,
        credentials: dict[str, Any],
        expires_at: datetime | None = None,
    ) -> str:
        """Encrypt a credential dict into a storable ciphertext string."""
        return encrypt_credential_envelope(
            self._key,
            institution_id=institution_id,
            vendor=f"db_direct:{backend}",
            credentials=credentials,
            issued_at=datetime.now(UTC),
            expires_at=expires_at,
        )

    def open(self, stored: str) -> DbCredentials:
        """Decrypt for one pull cycle. Discard the result afterwards; never
        persist or log the plaintext."""
        if not stored:
            msg = "No stored database-direct credential to open."
            raise CredentialVaultError(msg)
        envelope = decrypt_credential_envelope(self._key, stored)
        creds = dict(envelope.get("credentials", {}))
        username = str(creds.get(_USERNAME_KEY, ""))
        if not username:
            msg = "Stored database-direct credential has no username."
            raise CredentialVaultError(msg)
        extra_raw = creds.get(_EXTRA_KEY, {})
        extra = (
            {str(k): str(v) for k, v in extra_raw.items()} if isinstance(extra_raw, dict) else {}
        )
        return DbCredentials(
            username=username,
            password=str(creds.get(_PASSWORD_KEY, "")),
            extra=extra,
        )

    def fingerprint(self, credentials: dict[str, Any]) -> str:
        return credential_fingerprint(credentials)
