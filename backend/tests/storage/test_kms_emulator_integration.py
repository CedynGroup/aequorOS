"""Production key-provider contract over HTTP against a disposable local KMS."""

from __future__ import annotations

import os
import socket
from collections.abc import Iterator
from typing import Protocol, cast

import pytest
from pydantic import BaseModel, ConfigDict

from app.core.key_management.aws import AwsKmsKeyProvider, kms_client
from app.core.key_management.settings import get_key_settings
from app.core.key_management.types import (
    KeyIntegrityError,
    KeyReference,
    KeyStatus,
    KeyUnavailableError,
)

pytestmark = pytest.mark.requires_service("kms")


class _Key(BaseModel):
    model_config = ConfigDict(extra="ignore")
    Arn: str
    AWSAccountId: str


class _Created(BaseModel):
    model_config = ConfigDict(extra="ignore")
    KeyMetadata: _Key


class _Admin(Protocol):
    def create_key(self, *, Description: str) -> object: ...
    def disable_key(self, *, KeyId: str) -> object: ...
    def enable_key(self, *, KeyId: str) -> object: ...
    def schedule_key_deletion(self, *, KeyId: str, PendingWindowInDays: int) -> object: ...


@pytest.fixture
def emulator(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[_Admin, KeyReference, KeyReference]]:
    endpoint = os.environ.get("TEST_KMS_ENDPOINT_URL")
    if not endpoint:
        pytest.skip("TEST_KMS_ENDPOINT_URL selects the disposable KMS emulator")
    monkeypatch.setenv("BANK_KMS_ENDPOINT_URL", endpoint)
    get_key_settings.cache_clear()
    client = kms_client("us-east-1")
    admin = cast(_Admin, cast(object, client))
    keys: list[KeyReference] = []
    try:
        for _ in range(2):
            metadata = _Created.model_validate(
                admin.create_key(Description="aequorOS synthetic encryption contract")
            ).KeyMetadata
            keys.append(KeyReference("aws_kms", metadata.Arn, "us-east-1", metadata.AWSAccountId))
        yield admin, keys[0], keys[1]
    finally:
        for key in keys:
            _ = admin.schedule_key_deletion(KeyId=key.key_id, PendingWindowInDays=7)
        client.close()
        get_key_settings.cache_clear()


def test_real_provider_encryption_revocation_recovery_and_rotation(
    emulator: tuple[_Admin, KeyReference, KeyReference],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin, source, destination = emulator
    provider = AwsKmsKeyProvider()
    context = {"bank_id": "BK-SAMP0001", "purpose": "synthetic-kms-contract"}
    assert provider.describe(source).status == KeyStatus.ACTIVE
    data = provider.generate_data_key(source, context)
    assert len(data.plaintext) == 32 and data.plaintext not in data.wrapped
    assert AwsKmsKeyProvider().unwrap(source, data.wrapped, context) == data.plaintext
    with pytest.raises(KeyIntegrityError):
        provider.unwrap(source, data.wrapped, {**context, "bank_id": "BK-OTHER001"})
    _ = admin.disable_key(KeyId=source.key_id)
    assert provider.describe(source).status == KeyStatus.DISABLED
    with pytest.raises(KeyUnavailableError) as refusal:
        provider.unwrap(source, data.wrapped, context)
    assert refusal.value.revoked
    with pytest.raises(KeyUnavailableError):
        provider.generate_data_key(source, context)
    _ = admin.enable_key(KeyId=source.key_id)
    assert AwsKmsKeyProvider().unwrap(source, data.wrapped, context) == data.plaintext
    rotated = provider.rotate(source, destination, data.wrapped, context)
    _ = admin.disable_key(KeyId=source.key_id)
    assert AwsKmsKeyProvider().unwrap(destination, rotated, context) == data.plaintext

    with socket.socket() as unused:
        unused.bind(("127.0.0.1", 0))
        port = cast(tuple[str, int], unused.getsockname())[1]
        # A bound, non-listening socket deterministically refuses connections.
        with monkeypatch.context() as outage:
            outage.setenv("BANK_KMS_ENDPOINT_URL", f"http://127.0.0.1:{port}")
            get_key_settings.cache_clear()
            with pytest.raises(KeyUnavailableError) as unavailable:
                AwsKmsKeyProvider().unwrap(destination, rotated, context)
            assert not unavailable.value.revoked
    get_key_settings.cache_clear()
    assert AwsKmsKeyProvider().unwrap(destination, rotated, context) == data.plaintext
