"""Filing signatures for regulatory filings sent through the reporting gateway.

A filing is signed so the regulator can prove which institution sent which
bytes. Signing keys are asymmetric (ECC NIST P-256, ECDSA with SHA-256,
published as ES256) and are separate from the symmetric bank master key that
wraps stored data. Two custodians exist:

* ``vault`` (the default): AequorOS creates one signing key per bank when the
  bank is enrolled on the gateway and keeps it encrypted in the platform
  credential vault, bound to that bank. It is decrypted for one filing only,
  after the bank's own preparer and approver have approved the return; nothing
  else in AequorOS can produce a filing signature (:class:`VaultSigningProvider`).
* ``aws_kms``: a key in the bank's own key service, for a bank that wants to
  hold its key itself. AequorOS asks the service to sign a SHA-256 digest and
  the service records every use. It reuses the reference type and ownership
  rules of the bank master key
  (:func:`app.core.key_management.aws.validate_reference`): an exact key ARN, in
  the bank's declared account and region, never in the platform account.
"""

from __future__ import annotations

import base64
import functools
import hashlib
from collections.abc import Callable
from typing import Protocol, cast

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import (
    Prehashed,
    decode_dss_signature,
)
from pydantic import BaseModel, ConfigDict

from app.core.config import is_undeployed_environment
from app.core.key_management.aws import kms_client, validate_reference
from app.core.key_management.types import KeyReference, KeyUnavailableError

SIGNING_ALGORITHM = "ECDSA_SHA_256"
KEY_SPEC = "ECC_NIST_P256"


class SigningProvider(Protocol):
    def public_key(self, key: KeyReference) -> ec.EllipticCurvePublicKey: ...

    def sign_digest(self, key: KeyReference, digest: bytes) -> bytes:
        """DER-encoded ECDSA signature over a 32-byte SHA-256 digest."""
        ...


class _PublicKeyResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    PublicKey: bytes
    KeySpec: str
    KeyUsage: str
    SigningAlgorithms: list[str] = []


class _SignResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    Signature: bytes


class _SigningClient(Protocol):
    def get_public_key(self, *, KeyId: str) -> object: ...

    def sign(
        self, *, KeyId: str, Message: bytes, MessageType: str, SigningAlgorithm: str
    ) -> object: ...


class AwsKmsSigningProvider:
    """Signs with an asymmetric KMS key in the bank's own AWS account."""

    def __init__(self, client_factory: Callable[[str], object] | None = None) -> None:
        self._client_factory: Callable[[str], object] = client_factory or kms_client

    def _client(self, key: KeyReference) -> _SigningClient:
        validate_reference(key)
        return cast(_SigningClient, self._client_factory(key.region))

    def public_key(self, key: KeyReference) -> ec.EllipticCurvePublicKey:
        response = _PublicKeyResponse.model_validate(
            self._client(key).get_public_key(KeyId=key.key_id)
        )
        if response.KeySpec != KEY_SPEC or response.KeyUsage != "SIGN_VERIFY":
            raise KeyUnavailableError(
                "The filing signature key must be an ECC NIST P-256 key for signing."
            )
        if SIGNING_ALGORITHM not in response.SigningAlgorithms:
            raise KeyUnavailableError("The filing signature key must allow ECDSA with SHA-256.")
        loaded = serialization.load_der_public_key(response.PublicKey)
        if not isinstance(loaded, ec.EllipticCurvePublicKey):
            raise KeyUnavailableError("The filing signature key is not an elliptic-curve key.")
        return loaded

    def sign_digest(self, key: KeyReference, digest: bytes) -> bytes:
        if len(digest) != hashlib.sha256().digest_size:
            raise ValueError("A SHA-256 digest is required.")
        response = _SignResponse.model_validate(
            self._client(key).sign(
                KeyId=key.key_id,
                Message=digest,
                MessageType="DIGEST",
                SigningAlgorithm=SIGNING_ALGORITHM,
            )
        )
        return response.Signature


class LocalSigningProvider:
    """In-process signing keys for local development and tests only."""

    def __init__(self) -> None:
        if not is_undeployed_environment():
            raise KeyUnavailableError("Local signing keys are permitted only in local/test.")
        self._keys: dict[KeyReference, ec.EllipticCurvePrivateKey] = {}

    def add_key(
        self, key: KeyReference, private_key: ec.EllipticCurvePrivateKey | None = None
    ) -> ec.EllipticCurvePrivateKey:
        material = private_key or ec.generate_private_key(ec.SECP256R1())
        self._keys[key] = material
        return material

    def _material(self, key: KeyReference) -> ec.EllipticCurvePrivateKey:
        try:
            return self._keys[key]
        except KeyError as exc:
            raise KeyUnavailableError("The filing signature key is not available.") from exc

    def public_key(self, key: KeyReference) -> ec.EllipticCurvePublicKey:
        return self._material(key).public_key()

    def sign_digest(self, key: KeyReference, digest: bytes) -> bytes:
        if len(digest) != hashlib.sha256().digest_size:
            raise ValueError("A SHA-256 digest is required.")
        return self._material(key).sign(digest, ec.ECDSA(Prehashed(hashes.SHA256())))


class VaultSigningProvider:
    """A bank's platform-held signing key, decrypted from the vault for one use."""

    def __init__(self, private_key_pem: str) -> None:
        self._key = load_p256_private_key(private_key_pem)

    def public_key(self, key: KeyReference) -> ec.EllipticCurvePublicKey:
        del key
        return self._key.public_key()

    def sign_digest(self, key: KeyReference, digest: bytes) -> bytes:
        del key
        if len(digest) != hashlib.sha256().digest_size:
            raise ValueError("A SHA-256 digest is required.")
        return self._key.sign(digest, ec.ECDSA(Prehashed(hashes.SHA256())))


def generate_p256_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def private_key_pem(key: ec.EllipticCurvePrivateKey) -> str:
    """PKCS#8 PEM for storing a key in the credential vault (never anywhere else)."""
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")


def load_p256_private_key(pem: str) -> ec.EllipticCurvePrivateKey:
    try:
        loaded = serialization.load_pem_private_key(pem.encode(), password=None)
    except ValueError as exc:
        raise KeyUnavailableError("The key could not be read.") from exc
    if not isinstance(loaded, ec.EllipticCurvePrivateKey) or not isinstance(
        loaded.curve, ec.SECP256R1
    ):
        raise KeyUnavailableError("The key must be an ECC NIST P-256 key.")
    return loaded


@functools.cache
def local_signing_provider() -> LocalSigningProvider:
    """The process-wide local provider (local/test only)."""
    return LocalSigningProvider()


def signing_provider_for(provider: str) -> SigningProvider:
    if provider == "aws_kms":
        return AwsKmsSigningProvider()
    if provider == "local":
        return local_signing_provider()
    raise KeyUnavailableError(f"Unsupported signing key provider '{provider}'.")


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def es256_signature(der: bytes) -> bytes:
    """A DER ECDSA signature as the raw 64-byte ``R || S`` that JWS ES256 uses."""
    r, s = decode_dss_signature(der)
    return r.to_bytes(32, "big") + s.to_bytes(32, "big")


def public_jwk(public_key: ec.EllipticCurvePublicKey, kid: str) -> dict[str, str]:
    numbers = public_key.public_numbers()
    return {
        "kty": "EC",
        "crv": "P-256",
        "x": b64url(numbers.x.to_bytes(32, "big")),
        "y": b64url(numbers.y.to_bytes(32, "big")),
        "kid": kid,
        "alg": "ES256",
        "use": "sig",
    }


def jwk_thumbprint(public_key: ec.EllipticCurvePublicKey) -> str:
    """RFC 7638 SHA-256 thumbprint of the EC public key, base64url."""
    jwk = public_jwk(public_key, kid="")
    canonical = (
        '{"crv":"' + jwk["crv"] + '","kty":"EC","x":"' + jwk["x"] + '","y":"' + jwk["y"] + '"}'
    )
    return b64url(hashlib.sha256(canonical.encode("ascii")).digest())


def sign_compact_jws(
    provider: SigningProvider,
    key: KeyReference,
    *,
    header: dict[str, str],
    payload: bytes,
    encode_json: Callable[[object], bytes],
) -> str:
    """Compact JWS (ES256) over ``payload``, signed by the key service.

    ``encode_json`` renders the protected header canonically (RFC 8785).
    """
    if header.get("alg") != "ES256":
        raise ValueError("Only ES256 filing signatures are supported.")
    signing_input = b64url(encode_json(header)) + "." + b64url(payload)
    digest = hashlib.sha256(signing_input.encode("ascii")).digest()
    signature = es256_signature(provider.sign_digest(key, digest))
    return signing_input + "." + b64url(signature)
