from __future__ import annotations

# pyright: reportMissingTypeStubs=false
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast
from uuid import uuid4

import boto3
import pytest
from botocore.client import BaseClient
from botocore.exceptions import ClientError
from botocore.stub import Stubber
from moto import mock_aws
from pydantic import BaseModel, ConfigDict

from app.core.config import get_settings
from app.core.key_management.aws import AwsKmsKeyProvider
from app.core.key_management.local import FileTestKeyProvider, LocalKeyProvider
from app.core.key_management.types import (
    KeyIntegrityError,
    KeyProvider,
    KeyReference,
    KeyStatus,
    KeyUnavailableError,
)


class _Admin(Protocol):
    def create_key(self, *, KeyUsage: str) -> object: ...

    def disable_key(self, *, KeyId: str) -> object: ...

    def describe_key(self, *, KeyId: str) -> object: ...


class _Stub(Protocol):
    def add_response(
        self, method: str, response: dict[str, object], expected_params: dict[str, str]
    ) -> None: ...
    def add_client_error(
        self, method: str, *, service_error_code: str, service_message: str
    ) -> None: ...
    def assert_no_pending_responses(self) -> None: ...


class _KeyMetadata(BaseModel):
    model_config = ConfigDict(extra="ignore")
    Arn: str


class _Created(BaseModel):
    model_config = ConfigDict(extra="ignore")
    KeyMetadata: _KeyMetadata


@dataclass
class ProviderCase:
    provider: KeyProvider
    source: KeyReference
    destination: KeyReference
    refuse: Callable[[], object]


@pytest.fixture(params=["local", "aws_kms"])
def case(request: pytest.FixtureRequest) -> Iterator[ProviderCase]:
    if cast(object, request.param) == "local":
        provider = LocalKeyProvider()
        source = KeyReference("local", "bank-a-key-1", "local", "bank-a")
        destination = KeyReference("local", "bank-a-key-2", "local", "bank-a")
        provider.add_key(source)
        provider.add_key(destination)
        yield ProviderCase(
            provider, source, destination, lambda: provider.set_status(source, KeyStatus.DISABLED)
        )
    else:
        with mock_aws():
            factory = cast(Callable[..., BaseClient], boto3.client)
            client = factory("kms", region_name="us-east-1")
            admin = cast(_Admin, cast(object, client))
            source = KeyReference(
                "aws_kms",
                _Created.model_validate(
                    admin.create_key(KeyUsage="ENCRYPT_DECRYPT")
                ).KeyMetadata.Arn,
                "us-east-1",
                "123456789012",
            )
            destination = KeyReference(
                "aws_kms",
                _Created.model_validate(
                    admin.create_key(KeyUsage="ENCRYPT_DECRYPT")
                ).KeyMetadata.Arn,
                "us-east-1",
                "123456789012",
            )
            yield ProviderCase(
                AwsKmsKeyProvider(lambda _region: client),
                source,
                destination,
                lambda: admin.disable_key(KeyId=source.key_id.rsplit("/", 1)[-1]),
            )


_CONTEXT = {"bank_id": "BK-SAMP0001", "purpose": "object-data-key"}


def test_wrap_unwrap_and_generation_conformance(case: ProviderCase) -> None:
    assert case.provider.describe(case.source).status == KeyStatus.ACTIVE
    data_key = case.provider.generate_data_key(case.source, _CONTEXT)
    assert len(data_key.plaintext) == 32
    assert data_key.plaintext not in data_key.wrapped
    assert case.provider.unwrap(case.source, data_key.wrapped, _CONTEXT) == data_key.plaintext
    second = case.provider.generate_data_key(case.source, _CONTEXT)
    assert second.plaintext != data_key.plaintext


def test_revocation_refuses_even_after_successful_unwrap(case: ProviderCase) -> None:
    data_key = case.provider.generate_data_key(case.source, _CONTEXT)
    assert case.provider.unwrap(case.source, data_key.wrapped, _CONTEXT) == data_key.plaintext
    case.refuse()
    assert case.provider.describe(case.source).status == KeyStatus.DISABLED
    with pytest.raises(KeyUnavailableError):
        case.provider.unwrap(case.source, data_key.wrapped, _CONTEXT)
    with pytest.raises(KeyUnavailableError):
        case.provider.generate_data_key(case.source, _CONTEXT)


def test_rotation_preserves_data_key_and_survives_old_key_revocation(case: ProviderCase) -> None:
    data_key = case.provider.generate_data_key(case.source, _CONTEXT)
    rotated = case.provider.rotate(case.source, case.destination, data_key.wrapped, _CONTEXT)
    case.refuse()
    assert case.provider.unwrap(case.destination, rotated, _CONTEXT) == data_key.plaintext


def test_context_and_ciphertext_are_authenticated(case: ProviderCase) -> None:
    data_key = case.provider.generate_data_key(case.source, _CONTEXT)
    with pytest.raises(KeyIntegrityError):
        case.provider.unwrap(case.source, data_key.wrapped, {**_CONTEXT, "bank_id": "BK-OTHER001"})
    corrupted = data_key.wrapped[:-1] + bytes([data_key.wrapped[-1] ^ 1])
    with pytest.raises(KeyIntegrityError):
        case.provider.unwrap(case.source, corrupted, _CONTEXT)


def test_local_outage_and_missing_key_fail_closed() -> None:
    provider = LocalKeyProvider()
    key = KeyReference("local", "missing", "local", "bank-a")
    assert provider.describe(key).status == KeyStatus.UNAVAILABLE
    with pytest.raises(KeyUnavailableError):
        provider.generate_data_key(key, _CONTEXT)
    provider.add_key(key)
    data_key = provider.generate_data_key(key, _CONTEXT)
    provider.set_status(key, KeyStatus.UNAVAILABLE)
    with pytest.raises(KeyUnavailableError):
        provider.unwrap(key, data_key.wrapped, _CONTEXT)


def test_aws_rejects_wrong_bank_account_before_network_access() -> None:
    def unexpected(_region: str) -> BaseClient:
        pytest.fail("invalid key reference must be refused before client construction")

    provider = AwsKmsKeyProvider(unexpected)
    key = KeyReference(
        "aws_kms", "arn:aws:kms:us-east-1:123456789012:key/test", "us-east-1", "999999999999"
    )
    with pytest.raises(KeyUnavailableError, match="bank's declared AWS account"):
        provider.describe(key)


def test_aws_revoked_decrypt_grant_refuses_after_warm_read() -> None:
    with mock_aws():
        factory = cast(Callable[..., BaseClient], boto3.client)
        client = factory("kms", region_name="us-east-1")
        admin = cast(_Admin, cast(object, client))
        key = KeyReference(
            "aws_kms",
            _Created.model_validate(admin.create_key(KeyUsage="ENCRYPT_DECRYPT")).KeyMetadata.Arn,
            "us-east-1",
            "123456789012",
        )
        provider = AwsKmsKeyProvider(lambda _region: client)
        data_key = provider.generate_data_key(key, _CONTEXT)
        assert provider.unwrap(key, data_key.wrapped, _CONTEXT) == data_key.plaintext
        description = cast(dict[str, object], admin.describe_key(KeyId=key.key_id))
        with Stubber(client) as stub:
            typed = cast(_Stub, cast(object, stub))
            typed.add_response("describe_key", description, {"KeyId": key.key_id})
            typed.add_client_error(
                "decrypt",
                service_error_code="AccessDeniedException",
                service_message="bank revoked the workload grant",
            )
            with pytest.raises(KeyUnavailableError):
                provider.unwrap(key, data_key.wrapped, _CONTEXT)
            typed.assert_no_pending_responses()


def test_aws_outage_reports_safe_failure() -> None:
    def unavailable(_region: str) -> BaseClient:
        raise ClientError(
            {"Error": {"Code": "ServiceUnavailable", "Message": "private-provider-diagnostic"}},
            "DescribeKey",
        )

    key = KeyReference(
        "aws_kms", "arn:aws:kms:us-east-1:123456789012:key/test", "us-east-1", "123456789012"
    )
    with pytest.raises(KeyUnavailableError) as error:
        AwsKmsKeyProvider(unavailable).describe(key)
    assert "private-provider-diagnostic" not in str(error.value)


def test_persistent_fake_is_explicit_and_refuses_revoked_key(tmp_path: Path) -> None:
    key = KeyReference("local_test", str(uuid4()), "local", "test-only")
    provider = FileTestKeyProvider(tmp_path)
    provider.add_key(key)
    data = provider.generate_data_key(key, {"bank_id": "BK-SAMP0001"})
    restarted = FileTestKeyProvider(tmp_path)
    assert restarted.unwrap(key, data.wrapped, {"bank_id": "BK-SAMP0001"}) == data.plaintext
    (tmp_path / key.key_id).unlink()
    assert restarted.describe(key).status == KeyStatus.UNAVAILABLE
    with pytest.raises(KeyUnavailableError):
        restarted.unwrap(key, data.wrapped, {"bank_id": "BK-SAMP0001"})


@pytest.mark.parametrize("environment", ["production", "staging", "preview", "unknown"])
def test_persistent_fake_is_never_available_in_deployment(
    environment: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APP_ENV", environment)
    get_settings.cache_clear()
    with pytest.raises(KeyUnavailableError, match="forbidden"):
        FileTestKeyProvider(tmp_path)
