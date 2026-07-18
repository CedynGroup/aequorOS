"""The database-direct credential vault seam (reuses market-data crypto)."""

from __future__ import annotations

import pytest

from app.adapters.database_direct.credential_vault import (
    CredentialVaultError,
    DatabaseDirectCredentialVault,
    build_db_vault_path,
)

_MASTER_KEY = "unit-test-master-key-please-use-32-bytes-in-prod"


class TestSealOpen:
    def test_round_trip_username_password_and_extra(self) -> None:
        vault = DatabaseDirectCredentialVault(master_key=_MASTER_KEY)
        ciphertext = vault.seal(
            institution_id="bank-1",
            backend="sqlserver",
            credentials={
                "username": "SVC.AEQUOROS",
                "password": "s3cr3t",
                "extra": {"wallet_password": "w"},
            },
        )
        assert isinstance(ciphertext, str)
        assert "s3cr3t" not in ciphertext  # only opaque ciphertext is stored
        creds = vault.open(ciphertext)
        assert creds.username == "SVC.AEQUOROS"
        assert creds.password == "s3cr3t"
        assert creds.extra == {"wallet_password": "w"}

    def test_open_requires_username(self) -> None:
        vault = DatabaseDirectCredentialVault(master_key=_MASTER_KEY)
        ciphertext = vault.seal(
            institution_id="bank-1", backend="oracle", credentials={"password": "x"}
        )
        with pytest.raises(CredentialVaultError):
            vault.open(ciphertext)

    def test_open_empty_is_rejected(self) -> None:
        vault = DatabaseDirectCredentialVault(master_key=_MASTER_KEY)
        with pytest.raises(CredentialVaultError):
            vault.open("")

    def test_wrong_key_cannot_decrypt(self) -> None:
        sealed = DatabaseDirectCredentialVault(master_key=_MASTER_KEY).seal(
            institution_id="b", backend="odbc", credentials={"username": "u", "password": "p"}
        )
        other = DatabaseDirectCredentialVault(master_key="a-different-master-key-entirely")
        with pytest.raises(CredentialVaultError):
            other.open(sealed)


class TestFingerprintAndPath:
    def test_fingerprint_is_stable_and_order_independent(self) -> None:
        vault = DatabaseDirectCredentialVault(master_key=_MASTER_KEY)
        a = vault.fingerprint({"username": "u", "password": "p"})
        b = vault.fingerprint({"password": "p", "username": "u"})
        assert a == b
        assert len(a) == 64  # noqa: PLR2004 - SHA-256 hex width

    def test_missing_master_key_raises(self) -> None:
        with pytest.raises(CredentialVaultError):
            DatabaseDirectCredentialVault(master_key="")

    def test_vault_path_shape(self) -> None:
        assert (
            build_db_vault_path("bank-1", "sqlserver")
            == "vault://institutions/bank-1/db_direct/sqlserver/default"
        )
