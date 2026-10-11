from __future__ import annotations

import io
from collections.abc import Iterator
from urllib.parse import parse_qs, urlparse

import pytest
from pydantic import TypeAdapter
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.key_management.aws import AwsKmsKeyProvider
from app.core.key_management.models import BankEncryptionKey
from app.core.key_management.settings import get_key_settings
from app.core.key_management.types import DataKey, KeyReference, KeyUnavailableError
from app.db import session as database
from app.storage.client import StorageAccessError, StorageLocation
from app.storage.config import StorageEngineSettings
from app.storage.s3_compatible import S3CompatibleStorageClient
from tests.storage.contract import metadata_for
from tests.storage.test_bank_encryption import SLUG, BankStorage, bank_storage

__all__ = ["bank_storage"]


@pytest.fixture(autouse=True)
def rollout_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("BANK_KEY_REQUIRED", "false")
    get_key_settings.cache_clear()
    yield
    get_key_settings.cache_clear()
    get_settings.cache_clear()


def _storage(
    bank_storage: BankStorage, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> S3CompatibleStorageClient:
    monkeypatch.setattr(
        database,
        "get_sessionmaker",
        lambda: lambda: Session(db_session.get_bind(), join_transaction_mode="create_savepoint"),
    )
    settings = StorageEngineSettings(
        _env_file=None,  # type: ignore[call-arg] - pydantic-settings runtime option
        STORAGE_BACKEND="s3",
        STORAGE_ENV="dev",
        S3_ENDPOINT=None,
        STORAGE_KMS_KEY_ID="platform-key",
    )  # type: ignore[call-arg] - pydantic-settings runtime constructor options
    return S3CompatibleStorageClient(
        settings,
        client_factory=lambda *_args, **_kwargs: bank_storage.s3,
        access_log=bank_storage.log,
    )


@pytest.mark.parametrize("environment", ["local", "test", "staging", "production"])
def test_optional_key_keeps_platform_storage_and_required_key_refuses_it(
    bank_storage: BankStorage,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    environment: str,
) -> None:
    monkeypatch.setenv("APP_ENV", environment)
    get_settings.cache_clear()
    _ = db_session.execute(delete(BankEncryptionKey))
    db_session.commit()
    storage = _storage(bank_storage, db_session, monkeypatch)
    location = StorageLocation(SLUG, "outputs", "rollout/fixture.bin")
    content = b"synthetic platform file"
    metadata = metadata_for(SLUG, "outputs", content)
    written = storage.write(location, io.BytesIO(content), metadata)
    assert written.metadata.kms_key_id == "platform-key"
    stored = bank_storage.s3.get_object(
        Bucket=location.bucket_name("dev"), Key=location.object_path
    )
    assert stored["ServerSideEncryption"] == "aws:kms"
    assert "encryption-format" not in TypeAdapter(dict[str, str]).validate_python(
        stored["Metadata"]
    )
    _, stream = storage.read(location)
    with stream:
        assert stream.read() == content
    bank_storage.log.entries.clear()
    for operation in ("read", "write"):
        url = storage.presigned_url(location, operation)
        query = parse_qs(urlparse(url).query)
        assert "Signature" in query or "X-Amz-Signature" in query
        assert url not in bank_storage.log.export_jsonl()
        for credential in ("Signature", "X-Amz-Signature", "AWSAccessKeyId", "X-Amz-Credential"):
            for value in query.get(credential, []):
                assert value not in bank_storage.log.export_jsonl()
    assert [(entry.operation, entry.result) for entry in bank_storage.log.entries] == [
        ("presigned_url.read", "success"),
        ("presigned_url.write", "success"),
    ]
    monkeypatch.setenv("BANK_KEY_REQUIRED", "true")
    get_key_settings.cache_clear()
    with pytest.raises(StorageAccessError):
        storage.read(location)
    with pytest.raises(StorageAccessError):
        storage.write(
            location, io.BytesIO(b"replacement"), metadata_for(SLUG, "outputs", b"replacement")
        )
    with pytest.raises(StorageAccessError):
        storage.presigned_url(location, "write")


def test_connected_key_never_falls_back_when_enforcement_is_off(
    bank_storage: BankStorage, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = _storage(bank_storage, db_session, monkeypatch)
    calls: list[KeyReference] = []

    def refused(_self: AwsKmsKeyProvider, key: KeyReference, _context: dict[str, str]) -> DataKey:
        calls.append(key)
        raise KeyUnavailableError("The bank revoked access.")

    monkeypatch.setattr(AwsKmsKeyProvider, "generate_data_key", refused)
    location = StorageLocation(SLUG, "outputs", "rollout/connected.bin")
    with pytest.raises(StorageAccessError):
        storage.write(
            location, io.BytesIO(b"synthetic"), metadata_for(SLUG, "outputs", b"synthetic")
        )
    assert len(calls) == 1
    assert not storage.exists(location)
