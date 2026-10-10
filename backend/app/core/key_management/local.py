"""Development and conformance-test providers; never an AWS failure fallback."""

from __future__ import annotations

import secrets

from app.core.key_management import sdk
from app.core.key_management.types import (
    DataKey,
    KeyDescription,
    KeyProvider,
    KeyReference,
    KeyStatus,
    KeyUnavailableError,
)


class LocalKeyProvider(KeyProvider):
    def __init__(self) -> None:
        self._keys: dict[KeyReference, bytes] = {}
        self._statuses: dict[KeyReference, KeyStatus] = {}

    def add_key(self, key: KeyReference) -> None:
        self._keys[key] = secrets.token_bytes(32)
        self._statuses[key] = KeyStatus.ACTIVE

    def set_status(self, key: KeyReference, status: KeyStatus) -> None:
        self._statuses[key] = status

    def describe(self, key: KeyReference) -> KeyDescription:
        return KeyDescription(key, self._statuses.get(key, KeyStatus.UNAVAILABLE))

    def _material(self, key: KeyReference) -> bytes:
        if self.describe(key).status != KeyStatus.ACTIVE:
            raise KeyUnavailableError("The bank encryption key is unavailable.")
        return self._keys[key]

    def generate_data_key(self, key: KeyReference, context: dict[str, str]) -> DataKey:
        plaintext = secrets.token_bytes(32)
        return DataKey(plaintext, self.wrap(key, plaintext, context))

    def wrap(self, key: KeyReference, plaintext: bytes, context: dict[str, str]) -> bytes:
        return sdk.encrypt(
            plaintext, sdk.raw_keyring(self._material(key), name=key.key_id), context
        )

    def unwrap(self, key: KeyReference, wrapped: bytes, context: dict[str, str]) -> bytes:
        return sdk.decrypt(wrapped, sdk.raw_keyring(self._material(key), name=key.key_id), context)

    def rotate(
        self,
        source: KeyReference,
        destination: KeyReference,
        wrapped: bytes,
        context: dict[str, str],
    ) -> bytes:
        return self.wrap(destination, self.unwrap(source, wrapped, context), context)
