"""Market data's interface for other features: re-exports only, no logic."""

from app.adapters.market_data.credential_manager import (
    CredentialVaultError,
    credential_fingerprint,
    decrypt_credential_envelope,
    derive_master_key,
    encrypt_credential_envelope,
)
from app.services import market_data_sources
from app.services.market_data_sources import preferred_fx_spot

__all__ = [
    "CredentialVaultError",
    "credential_fingerprint",
    "decrypt_credential_envelope",
    "derive_master_key",
    "encrypt_credential_envelope",
    "market_data_sources",
    "preferred_fx_spot",
]
