"""Credential lifecycle management (market_data_adapter.md §10, storage.md §7).

Credentials are the bank's property, held in escrow: encrypted
per-institution, retrievable only for the bank's own pulls, revocable at any
time. The MVP vault is application-layer AES-256-GCM over the
``market_data_connections.credential_ciphertext`` column; the interface is a
Protocol so a HashiCorp-Vault-backed implementation can replace it without
touching callers.

Handling rules (market_data_adapter.md §15):
- Credentials are retrieved per pull cycle and discarded after use. No
  long-lived plaintext credentials in application memory.
- The decrypted credential dict must never be persisted, logged, or placed
  in error messages. Logs may carry only the SHA-256 fingerprint.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Protocol

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.adapters.market_data.base import CredentialSet
from app.core.config import get_settings
from app.models.market_data import MarketDataConnection

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.orm import Session

# §10.2: a credential expiring within this window is EXPIRING_SOON.
EXPIRING_SOON_THRESHOLD = timedelta(days=30)

_NONCE_BYTES = 12  # 96-bit random nonce per record, prepended to ciphertext.
_ENVELOPE_VERSION = 1


class CredentialVaultError(RuntimeError):
    """Vault misconfiguration or a missing/undecryptable credential record."""


class CredentialVault(Protocol):
    """The credential storage contract (§10.1).

    Implementations hold vendor credentials in escrow for an institution.
    ``store`` returns the logical vault path recorded on the connection;
    ``retrieve`` reconstructs a :class:`CredentialSet` for one pull cycle.
    """

    def store(
        self,
        *,
        organization_id: UUID,
        bank_id: UUID,
        vendor: str,
        credentials: dict[str, Any],
        expires_at: datetime | None = None,
    ) -> str: ...

    def retrieve(self, *, organization_id: UUID, bank_id: UUID, vendor: str) -> CredentialSet: ...

    def delete(self, *, organization_id: UUID, bank_id: UUID, vendor: str) -> None: ...

    def fingerprint(self, credentials: dict[str, Any]) -> str: ...


def build_vault_path(bank_id: UUID | str, vendor: str) -> str:
    """Logical credential locator per storage.md §7 / market_data_adapter.md §10.1."""
    return f"vault://institutions/{bank_id}/vendor_credentials/{vendor}/default"


def credential_fingerprint(credentials: dict[str, Any]) -> str:
    """SHA-256 over the canonical JSON of the credential dict.

    Stable across key ordering. This is the ONLY representation of a
    credential that may appear in logs or audit records.
    """
    canonical = json.dumps(credentials, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def derive_master_key(key_material: str) -> bytes:
    """Derive the 256-bit AES key from ``CREDENTIAL_VAULT_MASTER_KEY``.

    The env value is passed through SHA-256 so any non-empty string yields a
    uniform 32-byte key; operators should still provision high-entropy
    material (e.g. 32 random bytes, base64-encoded).
    """
    if not key_material:
        msg = "CREDENTIAL_VAULT_MASTER_KEY is empty; the credential vault cannot operate."
        raise CredentialVaultError(msg)
    return hashlib.sha256(key_material.encode("utf-8")).digest()


def encrypt_credential_envelope(  # noqa: PLR0913
    master_key: bytes,
    *,
    institution_id: str,
    vendor: str,
    credentials: dict[str, Any],
    issued_at: datetime,
    expires_at: datetime | None,
) -> str:
    """AES-256-GCM encrypt the credential envelope; returns base64 text.

    A fresh random 96-bit nonce is generated per record and prepended to the
    ciphertext, so identical plaintexts never produce identical stored values.
    """
    envelope = {
        "v": _ENVELOPE_VERSION,
        "institution_id": institution_id,
        "vendor": vendor,
        "credentials": credentials,
        "issued_at": issued_at.isoformat(),
        "expires_at": expires_at.isoformat() if expires_at is not None else None,
    }
    plaintext = json.dumps(envelope, sort_keys=True, separators=(",", ":"), default=str)
    nonce = os.urandom(_NONCE_BYTES)
    ciphertext = AESGCM(master_key).encrypt(nonce, plaintext.encode("utf-8"), None)
    return base64.b64encode(nonce + ciphertext).decode("ascii")


def decrypt_credential_envelope(master_key: bytes, stored: str) -> dict[str, Any]:
    """Reverse of :func:`encrypt_credential_envelope`. The returned dict is
    for one pull cycle only — never persist or log it."""
    try:
        blob = base64.b64decode(stored.encode("ascii"), validate=True)
        nonce, ciphertext = blob[:_NONCE_BYTES], blob[_NONCE_BYTES:]
        plaintext = AESGCM(master_key).decrypt(nonce, ciphertext, None)
        envelope = json.loads(plaintext.decode("utf-8"))
    except Exception as exc:
        msg = "Stored credential could not be decrypted (wrong master key or corrupt record)."
        raise CredentialVaultError(msg) from exc
    if not isinstance(envelope, dict):
        msg = "Decrypted credential envelope is not a mapping."
        raise CredentialVaultError(msg)
    return envelope


def derive_status(
    expires_at: datetime | None,
    last_validation_ok: bool | None,
    now: datetime,
) -> str:
    """Classify a credential into the §10.2 state machine.

    - ``EXPIRED``: past ``expires_at`` (expiration wins over validation
      failures — §10.2 defines INVALID as failing auth *for reasons other
      than expiration*).
    - ``INVALID``: the most recent ``validate_credentials`` check failed.
    - ``EXPIRING_SOON``: expires within :data:`EXPIRING_SOON_THRESHOLD`.
    - ``ACTIVE``: otherwise. ``last_validation_ok=None`` (never checked yet)
      is treated as not-known-bad.

    ``REVOKED`` and ``TESTING`` are operator/workflow-driven states set by
    the connection lifecycle, not derivable from expiry + health checks.
    """
    if expires_at is not None and expires_at <= now:
        return "EXPIRED"
    if last_validation_ok is False:
        return "INVALID"
    if expires_at is not None and expires_at <= now + EXPIRING_SOON_THRESHOLD:
        return "EXPIRING_SOON"
    return "ACTIVE"


class EncryptedDbVault:
    """MVP :class:`CredentialVault` backed by ``MarketDataConnection``.

    Encryption is application-layer AES-256-GCM with a key derived from
    ``CREDENTIAL_VAULT_MASTER_KEY`` (see :class:`~app.core.config.MarketDataSettings`);
    the DB column only ever sees base64 ciphertext. Retrieval decrypts for a
    single pull cycle; the caller must discard the plaintext after use and
    must never persist or log it (§15).
    """

    def __init__(self, db: Session, master_key: str | None = None) -> None:
        key_material = (
            master_key
            if master_key is not None
            else get_settings().market_data.credential_vault_master_key
        )
        if not key_material:
            msg = (
                "CREDENTIAL_VAULT_MASTER_KEY is not configured; "
                "the market data credential vault cannot operate."
            )
            raise CredentialVaultError(msg)
        self._db = db
        self._key = derive_master_key(key_material)

    def _connection(
        self, organization_id: UUID, bank_id: UUID, vendor: str
    ) -> MarketDataConnection:
        row = (
            self._db.query(MarketDataConnection)
            .filter(
                MarketDataConnection.organization_id == organization_id,
                MarketDataConnection.bank_id == bank_id,
                MarketDataConnection.vendor == vendor,
            )
            .one_or_none()
        )
        if row is None:
            msg = f"No market data connection for bank {bank_id} and vendor {vendor!r}."
            raise CredentialVaultError(msg)
        return row

    def store(
        self,
        *,
        organization_id: UUID,
        bank_id: UUID,
        vendor: str,
        credentials: dict[str, Any],
        expires_at: datetime | None = None,
    ) -> str:
        row = self._connection(organization_id, bank_id, vendor)
        vault_path = build_vault_path(bank_id, vendor)
        row.credential_ciphertext = encrypt_credential_envelope(
            self._key,
            institution_id=str(bank_id),
            vendor=vendor,
            credentials=credentials,
            issued_at=datetime.now(UTC),
            expires_at=expires_at,
        )
        row.credential_fingerprint = credential_fingerprint(credentials)
        row.credential_expires_at = expires_at
        row.vault_path = vault_path
        self._db.flush()
        return vault_path

    def retrieve(self, *, organization_id: UUID, bank_id: UUID, vendor: str) -> CredentialSet:
        """Decrypt for one pull cycle. Discard the result after the pull;
        never persist or log the plaintext credentials (§15)."""
        row = self._connection(organization_id, bank_id, vendor)
        stored = row.credential_ciphertext
        if not stored:
            msg = f"Connection for bank {bank_id} / vendor {vendor!r} holds no credential."
            raise CredentialVaultError(msg)
        envelope = decrypt_credential_envelope(self._key, stored)
        expires_raw = envelope.get("expires_at")
        return CredentialSet(
            institution_id=str(envelope.get("institution_id", bank_id)),
            vendor=str(envelope.get("vendor", vendor)),
            credentials=dict(envelope.get("credentials", {})),
            issued_at=datetime.fromisoformat(str(envelope["issued_at"])),
            expires_at=(
                datetime.fromisoformat(str(expires_raw)) if expires_raw is not None else None
            ),
        )

    def delete(self, *, organization_id: UUID, bank_id: UUID, vendor: str) -> None:
        """Wipe the stored ciphertext (cryptographic deletion of the record).

        The connection row itself is retained for audit (§10.5): revocation
        never deletes historical canonical data pulled with the credential.
        """
        row = self._connection(organization_id, bank_id, vendor)
        row.credential_ciphertext = None
        row.credential_fingerprint = None
        row.credential_expires_at = None
        self._db.flush()

    def fingerprint(self, credentials: dict[str, Any]) -> str:
        return credential_fingerprint(credentials)
