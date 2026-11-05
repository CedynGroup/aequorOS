"""Tests for test_credential_manager."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.identity.models.bank import Bank
from app.market_data.adapters.credential_manager import (
    CredentialVaultError,
    EncryptedDbVault,
    build_vault_path,
    credential_fingerprint,
)
from app.market_data.models.market_data import MarketDataConnection
from tests.market_data.adapters.test_credential_manager import CREDENTIALS
from tests.support.helpers import ORG_1


def test_encrypted_db_vault_roundtrip(db_session) -> None:
    bank = Bank(
        organization_id=ORG_1,
        name="Vault Test Bank",
        short_name="VTB",
        currency="GHS",
        jurisdiction_code="GH",
        license_type="universal",
        institution_type="universal_bank",
    )
    db_session.add(bank)
    db_session.flush()
    connection = MarketDataConnection(
        organization_id=ORG_1,
        bank_id=bank.id,
        vendor="refinitiv",
        display_name="Refinitiv Data Platform",
        status="TESTING",
        vault_path=build_vault_path(bank.id, "refinitiv"),
    )
    db_session.add(connection)
    db_session.flush()

    vault = EncryptedDbVault(db_session, master_key="unit-test-master-key")
    expires = datetime(2027, 1, 1, tzinfo=UTC)
    vault_path = vault.store(
        organization_id=ORG_1,
        bank_id=bank.id,
        vendor="refinitiv",
        credentials=CREDENTIALS,
        expires_at=expires,
    )
    assert vault_path == build_vault_path(bank.id, "refinitiv")
    assert connection.vault_path == vault_path
    assert connection.credential_ciphertext
    assert connection.credential_fingerprint == credential_fingerprint(CREDENTIALS)
    assert connection.credential_expires_at is not None
    for secret in CREDENTIALS.values():
        assert secret not in connection.credential_ciphertext

    retrieved = vault.retrieve(organization_id=ORG_1, bank_id=bank.id, vendor="refinitiv")
    assert retrieved.credentials == CREDENTIALS
    assert retrieved.vendor == "refinitiv"
    assert retrieved.expires_at == expires

    vault.delete(organization_id=ORG_1, bank_id=bank.id, vendor="refinitiv")
    assert connection.credential_ciphertext is None
    assert connection.credential_fingerprint is None
    with pytest.raises(CredentialVaultError, match="holds no credential"):
        vault.retrieve(organization_id=ORG_1, bank_id=bank.id, vendor="refinitiv")


def test_encrypted_db_vault_missing_connection_fails_loudly(db_session) -> None:
    vault = EncryptedDbVault(db_session, master_key="unit-test-master-key")
    with pytest.raises(CredentialVaultError, match="No market data connection"):
        vault.retrieve(organization_id=ORG_1, bank_id="BK-ZZZZZZZZ", vendor="bloomberg")


def test_encrypted_db_vault_requires_master_key(db_session) -> None:
    with pytest.raises(CredentialVaultError, match="CREDENTIAL_VAULT_MASTER_KEY"):
        EncryptedDbVault(db_session, master_key="")
