from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import BinaryIO, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.key_management import sdk
from app.core.key_management.models import BankEncryptionKey
from app.core.key_management.registry import DatabaseEnvelopeStore, EnvelopeStore
from app.core.key_management.settings import get_key_settings
from app.core.key_management.types import KeyUnavailableError
from app.db import session as database
from app.storage.client import ObjectMetadata, StorageAccessError, StorageLocation

ENCRYPTION_FORMAT = "aws-esdk-object-v1"


def object_context(location: StorageLocation, checksum: str) -> dict[str, str]:
    return {
        "institution_slug": location.institution_slug,
        "tier": location.tier,
        "object_path": location.object_path,
        "checksum_sha256": checksum,
        "purpose": "bank-object",
    }


class ObjectEncryption:
    def __init__(self, store: EnvelopeStore | None = None) -> None:
        self._store = store
        self._injected_store = store is not None

    def bank_key_required(self, location: StorageLocation) -> bool:
        """Connected keys always fail closed, independently of the rollout flag."""
        if self._injected_store or get_key_settings().bank_key_required:
            return True
        sessions = cast(Callable[[], Callable[[], Session]], database.get_sessionmaker)()
        with sessions() as db:
            return (
                db.scalar(
                    select(BankEncryptionKey.id).where(
                        BankEncryptionKey.storage_slug == location.institution_slug
                    )
                )
                is not None
            )

    def _envelopes(self) -> EnvelopeStore:
        if self._store is None:
            sessions = cast(Callable[[], Callable[[], Session]], database.get_sessionmaker)()
            self._store = DatabaseEnvelopeStore(sessions)
        return self._store

    def encrypt(
        self, location: StorageLocation, data: BinaryIO, metadata: ObjectMetadata
    ) -> tuple[BinaryIO, str | None, str | None]:
        try:
            if not self.bank_key_required(location):
                return data, None, None
            context = object_context(location, metadata.checksum_sha256)
            key = self._envelopes().prepare(location.institution_slug, context)
            encrypted = sdk.transform_file(data, key.plaintext, context)
            return encrypted, str(key.envelope_id), key.key_id
        except (KeyUnavailableError, RuntimeError, SQLAlchemyError) as exc:
            raise StorageAccessError(
                "The bank encryption key is unavailable; storage access refused."
            ) from exc

    def decrypt(
        self,
        location: StorageLocation,
        data: BinaryIO,
        metadata: ObjectMetadata,
        headers: dict[str, str],
    ) -> BinaryIO:
        close_source = True
        try:
            if "encryption-format" not in headers and not self.bank_key_required(location):
                close_source = False
                return data
            if headers.get("encryption-format") != ENCRYPTION_FORMAT:
                raise KeyUnavailableError("An encrypted bank object is required.")
            envelope_id = UUID(headers["key-envelope-id"])
            context = object_context(location, metadata.checksum_sha256)
            key = self._envelopes().open(location.institution_slug, envelope_id, context)
            decrypted = sdk.transform_file(data, key.plaintext, context, decrypting=True)
            digest = hashlib.sha256()
            for chunk in iter(lambda: decrypted.read(1024 * 1024), b""):
                digest.update(chunk)
            if digest.hexdigest() != metadata.checksum_sha256:
                decrypted.close()
                raise KeyUnavailableError("The encrypted object's checksum does not match.")
            _ = decrypted.seek(0)
            return decrypted
        except (KeyUnavailableError, ValueError, KeyError, RuntimeError, SQLAlchemyError) as exc:
            raise StorageAccessError(
                "The bank key or encrypted object could not be verified."
            ) from exc
        finally:
            if close_source:
                data.close()
