"""Bank-owned AWS KMS keys, accessed using the workload's AWS role.

Only exact key ARNs in the declared bank account are accepted.
KMS calls and SDK keyrings stay inside this adapter.
"""

from __future__ import annotations

# pyright: reportMissingTypeStubs=false
import re
import secrets
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Protocol, cast

import boto3
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


class _KmsClient(Protocol):
    def describe_key(self, *, KeyId: str) -> object: ...


class _ServiceError(BaseModel):
    model_config = ConfigDict(extra="ignore")
    Code: str = ""


class _ServiceResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    Error: _ServiceError | None = None


def _service_failure(payload: object) -> KeyUnavailableError | None:
    error = _ServiceResponse.model_validate(payload).Error
    if error is None or error.Code in {"InvalidCiphertextException", "IncorrectKeyException"}:
        return None
    return KeyUnavailableError(
        "The bank key service refused or could not complete the operation.",
        revoked=error.Code
        in {
            "AccessDenied",
            "AccessDeniedException",
            "DisabledException",
            "KMSInvalidStateException",
            "NotFoundException",
        },
    )


class _Events(Protocol):
    def register(self, event_name: str, handler: Callable[..., None]) -> None: ...
    def unregister(self, event_name: str, handler: Callable[..., None]) -> None: ...


class _MetadataEvents(Protocol):
    events: _Events


class _EventClient(Protocol):
    meta: _MetadataEvents


@dataclass
class _Failure:
    error: KeyUnavailableError | None = None

    def after_call(self, *, parsed: object, **_kwargs: object) -> None:
        self.error = _service_failure(parsed)

    def retry(self, *, caught_exception: object = None, **_kwargs: object) -> None:
        if caught_exception is not None:
            self.error = KeyUnavailableError("The bank key service is unreachable.")


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
        except ClientError as exc:
            failure = _service_failure(cast(object, exc.response))
            raise failure or KeyUnavailableError("The bank key service is unavailable.") from exc
        except Exception as exc:
            raise KeyUnavailableError(
                "The bank key service is unavailable or access was refused."
            ) from exc

    @contextmanager
    def _keyring(self, key: KeyReference) -> Generator[tuple[IKeyring, _Failure]]:
        description = self.describe(key)
        if description.status != KeyStatus.ACTIVE:
            raise KeyUnavailableError(
                "The bank encryption key is disabled or pending deletion.", revoked=True
            )
        client = self._factory(key.region)
        events = cast(_EventClient, cast(object, client)).meta.events
        failure = _Failure()
        events.register("after-call.kms.*", failure.after_call)
        events.register("needs-retry.kms.*", failure.retry)
        try:
            ring = sdk.materials().create_aws_kms_keyring(
                CreateAwsKmsKeyringInput(kms_key_id=key.key_id, kms_client=client),
            )
            yield ring, failure
        finally:
            events.unregister("after-call.kms.*", failure.after_call)
            events.unregister("needs-retry.kms.*", failure.retry)

    def generate_data_key(self, key: KeyReference, context: dict[str, str]) -> DataKey:
        plaintext = secrets.token_bytes(32)
        return DataKey(plaintext, self.wrap(key, plaintext, context))

    def wrap(self, key: KeyReference, plaintext: bytes, context: dict[str, str]) -> bytes:
        try:
            with self._keyring(key) as (ring, failure):
                try:
                    return sdk.encrypt(plaintext, ring, context)
                except Exception:
                    if failure.error is not None:
                        raise failure.error from None
                    raise
        except KeyUnavailableError:
            raise
        except Exception as exc:
            raise KeyUnavailableError("The bank key could not wrap the data key.") from exc

    def unwrap(self, key: KeyReference, wrapped: bytes, context: dict[str, str]) -> bytes:
        with self._keyring(key) as (ring, failure):
            try:
                return sdk.decrypt(wrapped, ring, context)
            except KeyIntegrityError:
                if failure.error is not None:
                    raise failure.error from None
                raise

    def rotate(
        self,
        source: KeyReference,
        destination: KeyReference,
        wrapped: bytes,
        context: dict[str, str],
    ) -> bytes:
        return self.wrap(destination, self.unwrap(source, wrapped, context), context)
