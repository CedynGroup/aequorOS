"""Tests for test_credential_manager."""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta

import pytest

from app.market_data.adapters.credential_manager import (
    CredentialVaultError,
    build_vault_path,
    credential_fingerprint,
    decrypt_credential_envelope,
    derive_master_key,
    derive_status,
    encrypt_credential_envelope,
)

MASTER_KEY = derive_master_key("unit-test-master-key")
CREDENTIALS = {
    "client_id": "rdp-app-8842",
    "client_secret": "s3cr3t-value-that-must-never-leak",
    "scope": "trapi.data.pricing.read",
}
ISSUED_AT = datetime(2026, 7, 1, 9, 0, tzinfo=UTC)


def _encrypt(expires_at: datetime | None = None) -> str:
    return encrypt_credential_envelope(
        MASTER_KEY,
        institution_id="bank-001",
        vendor="refinitiv",
        credentials=CREDENTIALS,
        issued_at=ISSUED_AT,
        expires_at=expires_at,
    )


def test_encrypt_decrypt_roundtrip() -> None:
    expires = datetime(2027, 7, 1, 9, 0, tzinfo=UTC)
    envelope = decrypt_credential_envelope(MASTER_KEY, _encrypt(expires))
    assert envelope["credentials"] == CREDENTIALS
    assert envelope["vendor"] == "refinitiv"
    assert envelope["institution_id"] == "bank-001"
    assert envelope["issued_at"] == ISSUED_AT.isoformat()
    assert envelope["expires_at"] == expires.isoformat()


def test_roundtrip_with_no_expiry() -> None:
    envelope = decrypt_credential_envelope(MASTER_KEY, _encrypt(None))
    assert envelope["expires_at"] is None


def test_ciphertext_contains_no_plaintext() -> None:
    stored = _encrypt()
    blob = base64.b64decode(stored)
    for secret in CREDENTIALS.values():
        assert secret.encode("utf-8") not in blob
        assert secret not in stored


def test_random_nonce_makes_ciphertext_unique() -> None:
    assert _encrypt() != _encrypt()


def test_decrypt_with_wrong_key_fails_loudly() -> None:
    wrong_key = derive_master_key("a-different-key")
    with pytest.raises(CredentialVaultError, match="could not be decrypted"):
        decrypt_credential_envelope(wrong_key, _encrypt())


def test_decrypt_garbage_fails_loudly() -> None:
    with pytest.raises(CredentialVaultError):
        decrypt_credential_envelope(MASTER_KEY, "not-even-base64!!!")


def test_derive_master_key_is_stable_and_256_bit() -> None:
    assert derive_master_key("k") == derive_master_key("k")
    assert derive_master_key("k") != derive_master_key("other")
    assert len(derive_master_key("k")) == 32


def test_derive_master_key_rejects_empty() -> None:
    with pytest.raises(CredentialVaultError, match="empty"):
        derive_master_key("")


def test_fingerprint_stable_across_key_order() -> None:
    reordered = dict(reversed(list(CREDENTIALS.items())))
    assert credential_fingerprint(CREDENTIALS) == credential_fingerprint(reordered)


def test_fingerprint_differs_for_different_credentials() -> None:
    other = {**CREDENTIALS, "client_secret": "rotated"}
    assert credential_fingerprint(CREDENTIALS) != credential_fingerprint(other)


def test_fingerprint_does_not_reveal_values() -> None:
    fingerprint = credential_fingerprint(CREDENTIALS)
    assert len(fingerprint) == 64
    for secret in CREDENTIALS.values():
        assert secret not in fingerprint


def test_build_vault_path_shape() -> None:
    # storage.md §7 / market_data_adapter.md §10.1 locator convention; the
    # credential-type segment is "default" per app/market_data/models/market_data.py.
    assert (
        build_vault_path("bank-001", "bloomberg")
        == "vault://institutions/bank-001/vendor_credentials/bloomberg/default"
    )


NOW = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("expires_at", "last_validation_ok", "expected"),
    [
        # Healthy, far from expiry.
        (NOW + timedelta(days=365), True, "ACTIVE"),
        # No expiry at all.
        (None, True, "ACTIVE"),
        # Never health-checked yet is not known-bad.
        (None, None, "ACTIVE"),
        # Inside the 30-day warning threshold (§10.2 default).
        (NOW + timedelta(days=29), True, "EXPIRING_SOON"),
        (NOW + timedelta(days=30), True, "EXPIRING_SOON"),
        # Just outside the threshold.
        (NOW + timedelta(days=30, seconds=1), True, "ACTIVE"),
        # Past expiry.
        (NOW - timedelta(seconds=1), True, "EXPIRED"),
        (NOW, True, "EXPIRED"),
        # Failing validation while unexpired.
        (NOW + timedelta(days=365), False, "INVALID"),
        (None, False, "INVALID"),
        # Expiration wins over validation failure (§10.2: INVALID is
        # non-expiration failure).
        (NOW - timedelta(days=1), False, "EXPIRED"),
        # Failing validation inside the warning window is still INVALID.
        (NOW + timedelta(days=10), False, "INVALID"),
    ],
)
def test_derive_status_transitions(
    expires_at: datetime | None, last_validation_ok: bool | None, expected: str
) -> None:
    assert derive_status(expires_at, last_validation_ok, NOW) == expected
