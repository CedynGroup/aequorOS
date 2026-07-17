"""Temenos credential vault: encrypt-at-rest round-trip, wrong-key rejection,
fingerprint stability, and cryptographic deletion — reusing the market-data
crypto over a TemenosConnection row."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.adapters.temenos_t24.credential_vault import (
    CredentialVaultError,
    TemenosCredentialVault,
)
from app.models import Bank, TemenosConnection
from tests.api.helpers import ORG_1

_CREDS = {"username": "SVC.AEQUOROS", "password": "s3cret-that-must-never-leak"}


def _connection(db_session: Session) -> TemenosConnection:
    bank = Bank(
        organization_id=ORG_1,
        name="Vault Test Bank",
        short_name="vault-test",
        currency="GHS",
        jurisdiction_code="GH",
        license_type="universal",
    )
    db_session.add(bank)
    db_session.flush()
    connection = TemenosConnection(
        organization_id=ORG_1,
        bank_id=bank.id,
        connection_mode="OFS",
        display_name="Core OFS",
        endpoint="ofs://bank",
        vault_path="",
    )
    db_session.add(connection)
    db_session.flush()
    return connection


def test_round_trip_reconstructs_credentials(db_session: Session) -> None:
    connection = _connection(db_session)
    vault = TemenosCredentialVault(db_session, master_key="unit-test-master-key")
    vault.store(connection, credentials=_CREDS)
    assert connection.credential_ciphertext
    assert _CREDS["password"] not in connection.credential_ciphertext  # encrypted at rest
    creds = vault.retrieve(connection)
    assert creds.username == "SVC.AEQUOROS"
    assert creds.password == _CREDS["password"]


def test_fingerprint_is_stable_and_set_on_store(db_session: Session) -> None:
    connection = _connection(db_session)
    vault = TemenosCredentialVault(db_session, master_key="unit-test-master-key")
    vault.store(connection, credentials=_CREDS)
    assert connection.credential_fingerprint == vault.fingerprint(_CREDS)
    reordered = dict(reversed(list(_CREDS.items())))
    assert connection.credential_fingerprint == vault.fingerprint(reordered)


def test_wrong_key_cannot_decrypt(db_session: Session) -> None:
    connection = _connection(db_session)
    TemenosCredentialVault(db_session, master_key="the-right-key").store(
        connection, credentials=_CREDS
    )
    wrong = TemenosCredentialVault(db_session, master_key="a-different-key")
    with pytest.raises(CredentialVaultError):
        wrong.retrieve(connection)


def test_delete_wipes_ciphertext_but_keeps_row(db_session: Session) -> None:
    connection = _connection(db_session)
    vault = TemenosCredentialVault(db_session, master_key="unit-test-master-key")
    vault.store(connection, credentials=_CREDS)
    vault.delete(connection)
    assert connection.credential_ciphertext is None
    assert connection.credential_fingerprint is None
    with pytest.raises(CredentialVaultError):
        vault.retrieve(connection)


def test_empty_master_key_refuses_to_operate(db_session: Session) -> None:
    with pytest.raises(CredentialVaultError, match="CREDENTIAL_VAULT_MASTER_KEY"):
        TemenosCredentialVault(db_session, master_key="")
