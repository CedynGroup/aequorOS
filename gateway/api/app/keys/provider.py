"""Gateway signing keys behind a ``KeyProvider`` interface.

``LocalFileKeyProvider`` serves ``local``, ``test`` and ``sandbox``: it keeps ES256 keys as
PKCS#8 PEM files in a gitignored directory and generates missing ones on first use.
Production uses a KMS/HSM provider holding the keys in the Bank's key service; the
interface only ever asks a key to sign bytes, so private keys never need to leave it.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from pydantic import JsonValue

from app.keys.jwk import public_jwk, sign_raw

if TYPE_CHECKING:
    from app.core.settings import Settings

KeyPurpose = Literal["receipt", "audit", "devidp"]


class KeyConfigurationError(RuntimeError):
    pass


class GatewayKey(Protocol):
    @property
    def kid(self) -> str: ...

    @property
    def public_key(self) -> ec.EllipticCurvePublicKey: ...

    def sign(self, data: bytes) -> bytes:
        """Raw ES256 ``R || S`` signature over ``data``."""
        ...


class KeyProvider(Protocol):
    def current(self, purpose: KeyPurpose) -> GatewayKey: ...

    def public_keys(self, purpose: KeyPurpose) -> list[GatewayKey]:
        """Current and retired-but-valid keys for verification."""
        ...

    def find(self, purpose: KeyPurpose, kid: str) -> GatewayKey | None: ...


@dataclass(frozen=True)
class LocalKey:
    kid: str
    private_key: ec.EllipticCurvePrivateKey

    @property
    def public_key(self) -> ec.EllipticCurvePublicKey:
        return self.private_key.public_key()

    def sign(self, data: bytes) -> bytes:
        return sign_raw(self.private_key, data)

    def jwk(self) -> dict[str, JsonValue]:
        return public_jwk(self.public_key, self.kid)


def load_or_create_private_key(path: Path, *, create: bool) -> ec.EllipticCurvePrivateKey:
    if path.exists():
        loaded = serialization.load_pem_private_key(path.read_bytes(), password=None)
        if not isinstance(loaded, ec.EllipticCurvePrivateKey) or not isinstance(
            loaded.curve, ec.SECP256R1
        ):
            raise KeyConfigurationError(f"{path.name} is not an EC P-256 private key")
        return loaded
    if not create:
        raise KeyConfigurationError(f"key file {path} is missing")
    path.parent.mkdir(parents=True, exist_ok=True)
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(pem)
    return key


class LocalFileKeyProvider:
    def __init__(
        self,
        directory: Path,
        kids: dict[KeyPurpose, str],
        *,
        retired: dict[KeyPurpose, list[str]] | None = None,
        create: bool = True,
    ) -> None:
        self._directory: Path = directory
        self._kids: dict[KeyPurpose, str] = kids
        self._retired: dict[KeyPurpose, list[str]] = retired or {}
        self._create: bool = create
        self._lock: threading.Lock = threading.Lock()
        self._cache: dict[str, LocalKey] = {}

    def _load(self, kid: str, *, create: bool) -> LocalKey:
        with self._lock:
            cached = self._cache.get(kid)
            if cached is not None:
                return cached
            key = LocalKey(
                kid, load_or_create_private_key(self._directory / f"{kid}.pem", create=create)
            )
            self._cache[kid] = key
            return key

    def current(self, purpose: KeyPurpose) -> LocalKey:
        return self._load(self._kids[purpose], create=self._create)

    def public_keys(self, purpose: KeyPurpose) -> list[GatewayKey]:
        keys: list[GatewayKey] = [self.current(purpose)]
        for kid in self._retired.get(purpose, []):
            if (self._directory / f"{kid}.pem").exists():
                keys.append(self._load(kid, create=False))
        return keys

    def find(self, purpose: KeyPurpose, kid: str) -> GatewayKey | None:
        for key in self.public_keys(purpose):
            if key.kid == kid:
                return key
        return None


def jwks_document(keys: list[GatewayKey]) -> dict[str, JsonValue]:
    return {"keys": [public_jwk(key.public_key, key.kid) for key in keys]}


def build_key_provider(settings: Settings) -> KeyProvider:
    """The key provider for the configured environment."""
    if not settings.uses_local_keys:
        raise KeyConfigurationError(
            "production needs a KMS/HSM key provider holding the Bank's keys; "
            "none is configured (local files are refused in production)"
        )
    retired = [k.strip() for k in settings.retired_receipt_key_ids.split(",") if k.strip()]
    return LocalFileKeyProvider(
        settings.keys_dir,
        {
            "receipt": settings.receipt_key_id,
            "audit": settings.audit_key_id,
            "devidp": settings.dev_oidc_key_id,
        },
        retired={"receipt": retired},
        create=True,
    )
