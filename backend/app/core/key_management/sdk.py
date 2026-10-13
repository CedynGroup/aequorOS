"""AWS Encryption SDK message handling; no application-owned cipher format."""

from __future__ import annotations

# pyright: reportMissingTypeStubs=false
from collections.abc import Iterator
from contextlib import AbstractContextManager
from tempfile import SpooledTemporaryFile
from typing import BinaryIO, Protocol, cast

import aws_encryption_sdk
from aws_cryptographic_material_providers.mpl import (
    AwsCryptographicMaterialProviders,
)
from aws_cryptographic_material_providers.mpl.config import (
    MaterialProvidersConfig,
)
from aws_cryptographic_material_providers.mpl.models import (
    AesWrappingAlg,
    CreateRawAesKeyringInput,
)
from aws_cryptographic_material_providers.mpl.references import (
    IKeyring,
)
from aws_encryption_sdk.identifiers import Algorithm

from app.core.key_management.types import KeyIntegrityError


class _Header(Protocol):
    encryption_context: dict[str, str]


class _Stream(Protocol):
    header: _Header

    def __iter__(self) -> Iterator[bytes]: ...


class _SdkClient(Protocol):
    def encrypt(
        self,
        *,
        source: bytes,
        keyring: IKeyring,
        encryption_context: dict[str, str],
        algorithm: object,
    ) -> tuple[bytes, _Header]: ...

    def decrypt(self, *, source: bytes, keyring: IKeyring) -> tuple[bytes, _Header]: ...

    def stream(self, **kwargs: object) -> AbstractContextManager[_Stream]: ...


def materials() -> AwsCryptographicMaterialProviders:
    return AwsCryptographicMaterialProviders(config=MaterialProvidersConfig())


def raw_keyring(key: bytes, *, name: str) -> IKeyring:
    return materials().create_raw_aes_keyring(
        CreateRawAesKeyringInput(
            key_namespace="aequoros",
            key_name=name,
            wrapping_key=key,
            wrapping_alg=AesWrappingAlg.ALG_AES256_GCM_IV12_TAG16,
        )
    )


def _client() -> _SdkClient:
    return cast(
        _SdkClient,
        cast(
            object,
            aws_encryption_sdk.EncryptionSDKClient(
                commitment_policy=aws_encryption_sdk.CommitmentPolicy.REQUIRE_ENCRYPT_REQUIRE_DECRYPT
            ),
        ),
    )


def encrypt(source: bytes, keyring: IKeyring, context: dict[str, str]) -> bytes:
    ciphertext, _header = _client().encrypt(
        source=source,
        keyring=keyring,
        encryption_context=context,
        algorithm=Algorithm.AES_256_GCM_HKDF_SHA512_COMMIT_KEY,
    )
    return ciphertext


def decrypt(source: bytes, keyring: IKeyring, context: dict[str, str]) -> bytes:
    try:
        plaintext, header = _client().decrypt(source=source, keyring=keyring)
        if header.encryption_context != context:
            raise KeyIntegrityError("Encrypted material context does not match.")
        return plaintext
    except KeyIntegrityError:
        raise
    except Exception as exc:
        raise KeyIntegrityError("Encrypted material could not be authenticated.") from exc


def transform_file(
    source: BinaryIO, key: bytes, context: dict[str, str], *, decrypting: bool = False
) -> BinaryIO:
    """Bounded-memory streaming; release plaintext only after full authentication.

    The spool is closed on failure and owned by the storage caller on success.
    A truncated or corrupt final frame never exposes earlier plaintext frames.
    """
    destination = SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b")  # noqa: SIM115 - ownership transfers to the caller
    parameters: dict[str, object] = {
        "mode": "d" if decrypting else "e",
        "source": source,
        "keyring": raw_keyring(key, name="object-data-key"),
    }
    if not decrypting:
        parameters["encryption_context"] = context
        parameters["algorithm"] = Algorithm.AES_256_GCM_HKDF_SHA512_COMMIT_KEY
    try:
        with _client().stream(**parameters) as stream:
            for chunk in stream:
                _ = destination.write(chunk)
            if stream.header.encryption_context != context:
                raise KeyIntegrityError("Encrypted object context does not match.")
        _ = destination.seek(0)
        return cast(BinaryIO, cast(object, destination))
    except Exception as exc:
        destination.close()
        raise KeyIntegrityError("Encrypted object could not be authenticated.") from exc
