"""Development and conformance-test providers; never an AWS failure fallback."""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from uuid import UUID

from sqlalchemy.engine import make_url

from app.core.config import get_settings, is_undeployed_environment
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


class FileTestKeyProvider(LocalKeyProvider):
    """Persistent fake for disposable SQLite fixtures across API processes.

    Accepts only explicit ``local_test`` key references. AWS failures never
    select this provider, and deployed environments always refuse it.
    """

    def __init__(self, directory: Path | None = None) -> None:
        super().__init__()
        if not is_undeployed_environment(os.getenv("APP_ENV") or None):
            raise KeyUnavailableError("Test encryption keys are forbidden in deployments.")
        if directory is None:
            database = make_url(get_settings().database.database_url or "sqlite://")
            if database.get_backend_name() != "sqlite" or not database.database:
                raise KeyUnavailableError("Test encryption requires a disposable SQLite file.")
            directory = Path(database.database).with_suffix(".bank-keys")
        self._directory = directory

    def _path(self, key: KeyReference) -> Path:
        if key.provider != "local_test" or key.owner_account != "test-only":
            raise KeyUnavailableError("A disposable test-key reference is required.")
        try:
            name = str(UUID(key.key_id))
        except ValueError as exc:
            raise KeyUnavailableError("A disposable test-key reference is required.") from exc
        return self._directory / name

    def add_key(self, key: KeyReference) -> None:
        path = self._path(key)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Exclusive creation never replaces a key that existing envelopes use.
        with path.open("xb") as output:
            path.chmod(0o600)
            _ = output.write(secrets.token_bytes(32))

    def _material(self, key: KeyReference) -> bytes:
        try:
            material = self._path(key).read_bytes()
        except OSError as exc:
            raise KeyUnavailableError("The disposable test key is unavailable.") from exc
        if len(material) != 32:
            raise KeyUnavailableError("The disposable test key is invalid.")
        return material

    def describe(self, key: KeyReference) -> KeyDescription:
        try:
            _ = self._material(key)
        except KeyUnavailableError:
            return KeyDescription(key, KeyStatus.UNAVAILABLE)
        return KeyDescription(key, KeyStatus.ACTIVE)
