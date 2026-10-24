"""Bank-owned AWS KMS keys, accessed using the workload's AWS role.

Only exact key ARNs in the declared bank account are accepted.
KMS calls and SDK keyrings stay inside this adapter.
"""

from __future__ import annotations

# pyright: reportMissingTypeStubs=false
import re
import secrets
from collections.abc import Callable, Iterator
from typing import Protocol, cast

import boto3
from aws_cryptographic_material_providers.mpl.errors import (
    CollectionOfErrors,
    ComAmazonawsKms,
    OpaqueError,
    OpaqueWithTextError,
)
from aws_cryptographic_material_providers.mpl.models import (
    CreateAwsKmsKeyringInput,
)
from aws_cryptographic_material_providers.mpl.references import (
    IKeyring,
)
from botocore.client import BaseClient
from botocore.config import Config
from botocore.exceptions import ClientError
from pydantic import BaseModel, ConfigDict

from app.core.config import get_settings, is_undeployed_environment
from app.core.key_management import sdk
from app.core.key_management.settings import get_key_settings
from app.core.key_management.types import (
    DataKey,
    KeyDescription,
    KeyIntegrityError,
    KeyProvider,
    KeyReference,
    KeyStatus,
    KeyUnavailableError,
)
from app.core.tls import require_boto_tls

_ARN = re.compile(r"^arn:(aws(?:-us-gov|-cn)?):kms:([^:]+):(\d{12}):key/(.+)$")


class _Metadata(BaseModel):
    model_config = ConfigDict(extra="ignore")
    Arn: str
    KeyState: str
    KeyManager: str
    KeyUsage: str
    KeySpec: str


class _Description(BaseModel):
    model_config = ConfigDict(extra="ignore")
    KeyMetadata: _Metadata


class _ClientFailure(Protocol):
    response: object
    operation_name: str


class _ErrorCode(BaseModel):
    model_config = ConfigDict(extra="ignore")
    Code: str = ""


class _ErrorResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    Error: _ErrorCode


class _KmsClient(Protocol):
    def describe_key(self, *, KeyId: str) -> object: ...


def validate_reference(key: KeyReference) -> None:
    match = _ARN.fullmatch(key.key_id)
    if match is None:
        raise KeyUnavailableError(
            "An exact AWS KMS key ARN is required; alias ARNs are not accepted."
        )
    if key.provider != "aws_kms" or match[2] != key.region or match[3] != key.owner_account:
        raise KeyUnavailableError("The key must be in the bank's declared AWS account and region.")
    platform = get_key_settings().platform_account
    if not is_undeployed_environment() and platform is None:
        raise KeyUnavailableError(
            "The platform AWS account must be configured to verify bank ownership."
        )
    if platform == key.owner_account:
        raise KeyUnavailableError(
            "The bank master key must be held outside the platform AWS account."
        )


def kms_client(region: str) -> BaseClient:
    factory = cast(Callable[..., BaseClient], boto3.client)
    client = factory(
        "kms",
        region_name=region,
        verify=get_settings().tls.ca_bundle or True,
        use_ssl=True,
        config=Config(connect_timeout=5, read_timeout=10, retries={"max_attempts": 2}),
    )
    require_boto_tls(client)
    return client


def _provider_errors(exc: Exception) -> Iterator[BaseException]:
    pending: list[object] = [exc]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, CollectionOfErrors):
            pending.extend(cast(list[object], current.list))
        if isinstance(current, (OpaqueError, OpaqueWithTextError)):
            pending.append(cast(object, current.obj))
        if isinstance(current, BaseException):
            yield current
            pending.extend([current.__cause__, current.__context__])


def _failure(exc: Exception, permission: str) -> KeyUnavailableError:
    for current in _provider_errors(exc):
        operation = ""
        if isinstance(current, ClientError):
            failure = cast(_ClientFailure, cast(object, current))
            operation = failure.operation_name
            code = _ErrorResponse.model_validate(failure.response).Error.Code
        elif isinstance(current, ComAmazonawsKms):
            # The MPL adapter discards botocore's structured response. Only
            # recognize its fixed error-code/operation prefix; never echo text.
            match = re.match(
                r"(?:ClientError\(['\"])?An error occurred \(([A-Za-z]+)\) when calling the "
                r"(DescribeKey|Encrypt|GenerateDataKey|Decrypt) operation:",
                current.message,
            )
            if match is None:
                continue
            code, operation = match.groups()
        else:
            continue
        permission = {
            "DescribeKey": "kms:DescribeKey",
            "Encrypt": "kms:Encrypt",
            "GenerateDataKey": "kms:GenerateDataKey",
            "Decrypt": "kms:Decrypt",
        }.get(operation, permission)
        if code in {"AccessDeniedException", "AccessDenied"}:
            return KeyUnavailableError(
                f"Access denied for {permission}. Add this permission to the bank key policy "
                "and the aequorOS workload role, then test again."
            )
        if code in {"DisabledException", "KMSInvalidStateException"}:
            return KeyUnavailableError(
                "The key is disabled or pending deletion. "
                "Enable the key in AWS KMS, then test again."
            )
        if code == "NotFoundException":
            return KeyUnavailableError(
                "Key not found. Check the exact key ARN, bank AWS account and region."
            )
    error = KeyIntegrityError if permission == "kms:Decrypt" else KeyUnavailableError
    return error(
        f"The key operation {permission} failed. Check KMS connectivity, key state "
        "and the required permission, then retry."
    )


class AwsKmsKeyProvider(KeyProvider):
    def __init__(self, client_factory: Callable[[str], BaseClient] | None = None) -> None:
        self._factory = client_factory or kms_client

    def describe(self, key: KeyReference) -> KeyDescription:
        validate_reference(key)
        try:
            client = cast(_KmsClient, cast(object, self._factory(key.region)))
            metadata = _Description.model_validate(
                client.describe_key(KeyId=key.key_id)
            ).KeyMetadata
            if metadata.Arn != key.key_id:
                raise KeyUnavailableError("The bank key service returned a different key ARN.")
            if (
                metadata.KeyManager != "CUSTOMER"
                or metadata.KeyUsage != "ENCRYPT_DECRYPT"
                or metadata.KeySpec != "SYMMETRIC_DEFAULT"
            ):
                raise KeyUnavailableError(
                    "A customer-managed symmetric encryption key is required."
                )
            state = KeyStatus.ACTIVE if metadata.KeyState == "Enabled" else KeyStatus.DISABLED
            return KeyDescription(key, state)
        except KeyUnavailableError:
            raise
        except Exception as exc:
            raise _failure(exc, "kms:DescribeKey") from exc

    def _keyring(self, key: KeyReference) -> IKeyring:
        description = self.describe(key)
        if description.status != KeyStatus.ACTIVE:
            raise KeyUnavailableError("The bank encryption key is disabled or pending deletion.")
        return sdk.materials().create_aws_kms_keyring(
            CreateAwsKmsKeyringInput(
                kms_key_id=key.key_id,
                kms_client=self._factory(key.region),
            )
        )

    def generate_data_key(self, key: KeyReference, context: dict[str, str]) -> DataKey:
        plaintext = secrets.token_bytes(32)
        return DataKey(plaintext, self.wrap(key, plaintext, context))

    def wrap(self, key: KeyReference, plaintext: bytes, context: dict[str, str]) -> bytes:
        try:
            return sdk.encrypt(plaintext, self._keyring(key), context)
        except KeyUnavailableError:
            raise
        except Exception as exc:
            raise _failure(exc, "kms:GenerateDataKey") from exc

    def unwrap(self, key: KeyReference, wrapped: bytes, context: dict[str, str]) -> bytes:
        keyring = self._keyring(key)
        try:
            return sdk.decrypt(wrapped, keyring, context)
        except Exception as exc:
            raise _failure(exc, "kms:Decrypt") from exc

    def rotate(
        self,
        source: KeyReference,
        destination: KeyReference,
        wrapped: bytes,
        context: dict[str, str],
    ) -> bytes:
        return self.wrap(destination, self.unwrap(source, wrapped, context), context)
