"""In-memory StorageClient for hermetic tests.

Implements the full contract — versioning, delete markers, logical vs
physical deletion, metadata validation — so API tests exercise real storage
semantics without network access, and the contract suite has a reference
subject that runs everywhere.
"""

from __future__ import annotations

import hashlib
import io
import logging
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import BinaryIO, Literal
from uuid import uuid4

from app.storage.access_log import AccessLogHook, HashChainedAccessLog, null_access_log
from app.storage.client import (
    RETAINED_TIERS,
    ObjectMetadata,
    SealedEncryptionContext,
    SealedPublication,
    StorageClient,
    StorageHealth,
    StorageLocation,
    StorageNotFoundError,
    StorageObject,
    StorageValidationError,
    Tier,
    is_sealed_object_key,
    refuse_sealed_maintenance,
)

logger = logging.getLogger(__name__)


@dataclass
class _Version:
    version_id: str
    content: bytes | None  # None = delete marker
    metadata: ObjectMetadata | None
    content_type: str
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class InMemoryStorageClient(StorageClient):
    def __init__(
        self,
        *,
        env: str = "mvp",
        access_log: AccessLogHook | None = None,
        kms_key_id: str | None = None,
    ) -> None:
        self._env = env
        self._log = access_log or null_access_log
        self._kms_key_id = kms_key_id
        self._objects: dict[tuple[str, str], list[_Version]] = {}
        self.audit_segments: list[str] = []

    def _key(self, location: StorageLocation) -> tuple[str, str]:
        return (location.bucket_name(self._env), location.object_path)

    def _current(self, location: StorageLocation) -> _Version | None:
        versions = self._objects.get(self._key(location), [])
        return versions[-1] if versions else None

    def write(
        self,
        location: StorageLocation,
        data: BinaryIO,
        metadata: ObjectMetadata,
        content_type: str = "application/octet-stream",
    ) -> StorageObject:
        refuse_sealed_maintenance(location, "a generic write")
        problems = []
        if not metadata.checksum_sha256:
            problems.append("checksum_sha256 is required")
        if not metadata.written_by:
            problems.append("written_by is required")
        if metadata.institution_slug != location.institution_slug:
            problems.append("metadata institution_slug does not match location")
        if metadata.tier != location.tier:
            problems.append("metadata tier does not match location")
        if problems:
            raise StorageValidationError("; ".join(problems))
        if metadata.lineage_node_id is None:
            logger.warning("storage write without lineage_node_id: %s", location)

        if metadata.kms_key_id is None and self._kms_key_id is not None:
            metadata = replace(metadata, kms_key_id=self._kms_key_id)

        current = self._current(location)
        if (
            current is not None
            and current.metadata is not None
            and current.metadata.checksum_sha256 == metadata.checksum_sha256
        ):
            self._log("write.noop", location, version_id=current.version_id)
            return self._describe(location, current)

        version = _Version(
            version_id=uuid4().hex,
            content=data.read(),
            metadata=metadata,
            content_type=content_type,
        )
        self._objects.setdefault(self._key(location), []).append(version)
        self._log("write", location, version_id=version.version_id)
        return self._describe(location, version)

    def write_sealed(
        self,
        location: StorageLocation,
        data: BinaryIO,
        metadata: ObjectMetadata,
        authenticated_context: StorageLocation,
        content_type: str = "application/octet-stream",
    ) -> SealedPublication:
        if location.tier != "outputs" or not is_sealed_object_key(location.object_path):
            raise StorageValidationError(
                "Sealed publication requires an immutable key in the outputs tier."
            )
        if (
            authenticated_context.institution_slug != location.institution_slug
            or authenticated_context.tier != location.tier
        ):
            raise StorageValidationError("Sealed object context must stay in its bank and tier.")
        payload = data.read()
        if hashlib.sha256(payload).hexdigest() != metadata.checksum_sha256:
            raise StorageValidationError(
                "Sealed publication content does not match its recorded checksum."
            )
        current = self._current(location)
        if current is not None and current.content is not None:
            if current.content != payload:
                raise StorageValidationError(
                    "Sealed publication refused: the immutable key already holds other content."
                )
            self._log("write.sealed.noop", location, version_id=current.version_id)
            return SealedPublication(self._describe(location, current), "plaintext-v1", None)
        version = _Version(
            version_id=uuid4().hex,
            content=payload,
            metadata=metadata,
            content_type=content_type,
        )
        self._objects.setdefault(self._key(location), []).append(version)
        self._log("write.sealed", location, version_id=version.version_id)
        return SealedPublication(self._describe(location, version), "plaintext-v1", None)

    def read_sealed(
        self, location: StorageLocation, context: SealedEncryptionContext
    ) -> tuple[StorageObject, BinaryIO]:
        descriptor, body = self.read(location)
        if descriptor.metadata.checksum_sha256 != context.sha256:
            body.close()
            raise StorageValidationError("Sealed object descriptor does not match its catalogue.")
        return descriptor, body

    def read(
        self,
        location: StorageLocation,
        version_id: str | None = None,
    ) -> tuple[StorageObject, BinaryIO]:
        version = self._resolve(location, version_id)
        self._log("read", location, version_id=version.version_id)
        assert version.content is not None
        return self._describe(location, version), io.BytesIO(version.content)

    def exists(self, location: StorageLocation) -> bool:
        current = self._current(location)
        found = current is not None and current.content is not None
        self._log("exists", location, result="found" if found else "missing")
        return found

    def list(
        self,
        institution_slug: str,
        tier: Tier,
        prefix: str = "",
        limit: int | None = None,
    ) -> Iterator[StorageObject]:
        base = StorageLocation(institution_slug, tier, prefix)
        self._log("list", base)
        bucket = base.bucket_name(self._env)
        yielded = 0
        for (object_bucket, path), versions in sorted(self._objects.items()):
            if object_bucket != bucket or not path.startswith(prefix):
                continue
            current = versions[-1]
            if current.content is None:
                continue
            if limit is not None and yielded >= limit:
                return
            yield self._describe(StorageLocation(institution_slug, tier, path), current)
            yielded += 1

    def list_versions(self, location: StorageLocation) -> Iterator[StorageObject]:
        self._log("list_versions", location)
        for version in self._objects.get(self._key(location), []):
            if version.content is not None:
                yield self._describe(location, version)

    def corrupt(self, location: StorageLocation, suffix: bytes = b"% tampered") -> None:
        """Replace the stored content, leaving the recorded metadata alone.

        Test support for the property that a verified read is what makes sealed
        evidence trustworthy: the bytes change underneath an object whose
        recorded identity does not. It deliberately bypasses the client's own
        refusal of the sealed namespace, because the corruption this simulates
        did not come through the client.
        """
        version = self._current(location)
        if version is None or version.content is None:
            raise StorageNotFoundError(f"No object at {location.object_path}")
        version.content = version.content + suffix

    def substitute(
        self,
        location: StorageLocation,
        content: bytes,
        *,
        record_checksum: bool = False,
    ) -> None:
        """Replace the stored content outright, optionally rewriting its metadata.

        ``record_checksum`` models an attacker who also rewrites the object's own
        metadata: only the recorded catalogue identity still contradicts them.
        """
        version = self._current(location)
        if version is None or version.content is None:
            raise StorageNotFoundError(f"No object at {location.object_path}")
        version.content = content
        if record_checksum and version.metadata is not None:
            version.metadata = replace(
                version.metadata, checksum_sha256=hashlib.sha256(content).hexdigest()
            )

    def lose(self, location: StorageLocation) -> None:
        """Delete the object outright, as a lost or expired object would be.

        Every version and delete marker goes, so the identity the caller records
        resolves to nothing rather than to a neighbouring object.
        """
        _ = self._objects.pop(self._key(location), None)

    def delete(self, location: StorageLocation) -> None:
        refuse_sealed_maintenance(location, "delete")
        key = self._key(location)
        if location.tier in RETAINED_TIERS:
            self._objects.setdefault(key, []).append(
                _Version(version_id=uuid4().hex, content=None, metadata=None, content_type="")
            )
            self._log("delete.logical", location)
            return
        self._objects.pop(key, None)
        self._log("delete.physical", location)

    def get_metadata(
        self,
        location: StorageLocation,
        version_id: str | None = None,
    ) -> ObjectMetadata:
        version = self._resolve(location, version_id)
        self._log("get_metadata", location, version_id=version.version_id)
        assert version.metadata is not None
        return version.metadata

    def presigned_url(
        self,
        location: StorageLocation,
        operation: Literal["read", "write"],
        expires_in_seconds: int = 900,
    ) -> str:
        self._log(f"presigned_url.{operation}", location)
        bucket = location.bucket_name(self._env)
        return f"memory://{bucket}/{location.object_path}?op={operation}&exp={expires_in_seconds}"

    def health_check(self) -> StorageHealth:
        return StorageHealth(healthy=True, backend="inmemory")

    def flush_access_log(self) -> str | None:
        if not isinstance(self._log, HashChainedAccessLog):
            return None
        segment = self._log.drain_segment()
        if segment is None:
            return None
        jsonl, first, last = segment
        self.audit_segments.append(jsonl)
        return f"aequoros-{self._env}-audit-logs/segment-{first:08d}-{last:08d}.jsonl"

    def _resolve(self, location: StorageLocation, version_id: str | None) -> _Version:
        versions = self._objects.get(self._key(location), [])
        if version_id is not None:
            for version in versions:
                if version.version_id == version_id and version.content is not None:
                    return version
            raise StorageNotFoundError(f"No version {version_id} at {location.object_path}")
        if versions and versions[-1].content is not None:
            return versions[-1]
        raise StorageNotFoundError(f"No object at {location.object_path}")

    def _describe(self, location: StorageLocation, version: _Version) -> StorageObject:
        assert version.metadata is not None
        return StorageObject(
            location=location,
            metadata=version.metadata,
            size_bytes=len(version.content or b""),
            version_id=version.version_id,
            created_at=version.created_at,
            content_type=version.content_type,
        )
