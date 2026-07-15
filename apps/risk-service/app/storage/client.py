"""The StorageClient contract (storage.md §4).

Application code — the Data Engine, calculation engines, audit substrate —
depends on this interface only. Concrete backends (MinIO/S3 today, GCS in
Phase 2) live beside it and must pass the shared contract-test suite; any
behavior difference between backends observable through this interface is a
bug in the backend, not a caller concern.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import BinaryIO, Literal

Tier = Literal["raw", "canonical", "outputs", "temp"]
TIERS: tuple[Tier, ...] = ("raw", "canonical", "outputs", "temp")
# Tiers whose deletes are logical-only; content is retained per lifecycle.
RETAINED_TIERS: tuple[Tier, ...] = ("raw", "canonical", "outputs")

StorageEnv = Literal["prod", "staging", "dev", "mvp"]


class StorageError(Exception):
    """Base for all storage layer errors."""


class StorageNotFoundError(StorageError):
    """Object does not exist at the requested location/version."""


class StorageAccessError(StorageError):
    """IAM or authentication denied the operation."""


class StorageValidationError(StorageError):
    """Metadata or content failed pre-flight validation."""


class StorageBackendError(StorageError):
    """Backend-level failure after retries (transient exhaustion, quotas)."""


@dataclass(frozen=True)
class StorageLocation:
    """Fully qualified location of an object in AequorOS storage.

    ``institution_slug`` is the DNS-safe institution identifier used in
    bucket names (e.g. ``sbl-gh-001``), not a database UUID. The concrete
    bucket name is resolved by the client from its environment config:
    ``aequoros-{env}-{institution_slug}-{tier}``.
    """

    institution_slug: str
    tier: Tier
    object_path: str

    def bucket_name(self, env: str) -> str:
        return f"aequoros-{env}-{self.institution_slug}-{self.tier}"


@dataclass(frozen=True)
class ObjectMetadata:
    """Custom metadata attached to every object at write time.

    ``checksum_sha256`` and ``written_by`` are always required. In production
    a missing ``lineage_node_id`` is a validation error; in dev/mvp it is a
    logged warning (storage.md §14.7) — enforced by the client, not callers.
    """

    institution_slug: str
    tier: Tier
    checksum_sha256: str
    written_at: datetime
    written_by: str
    as_of_date: str | None = None
    ingestion_batch_id: str | None = None
    lineage_node_id: str | None = None
    schema_version: str | None = None
    source_system: str | None = None
    source_reference: str | None = None
    kms_key_id: str | None = None

    def to_object_metadata(self) -> dict[str, str]:
        """Flatten to string key/values for backend custom-metadata headers."""
        values = {
            "institution-slug": self.institution_slug,
            "tier": self.tier,
            "checksum-sha256": self.checksum_sha256,
            "written-at": self.written_at.isoformat(),
            "written-by": self.written_by,
            "as-of-date": self.as_of_date,
            "ingestion-batch-id": self.ingestion_batch_id,
            "lineage-node-id": self.lineage_node_id,
            "schema-version": self.schema_version,
            "source-system": self.source_system,
            "source-reference": self.source_reference,
            "kms-key-id": self.kms_key_id,
        }
        return {key: value for key, value in values.items() if value is not None}

    @classmethod
    def from_object_metadata(cls, raw_headers: dict[str, str]) -> ObjectMetadata:
        # MinIO returns custom metadata keys Title-Cased while AWS returns
        # them lowercase; normalize so the difference never leaks to callers.
        raw = {key.lower(): value for key, value in raw_headers.items()}

        def get(key: str) -> str | None:
            return raw.get(key)

        return cls(
            institution_slug=raw["institution-slug"],
            tier=raw["tier"],  # type: ignore[arg-type]
            checksum_sha256=raw["checksum-sha256"],
            written_at=datetime.fromisoformat(raw["written-at"]),
            written_by=raw["written-by"],
            as_of_date=get("as-of-date"),
            ingestion_batch_id=get("ingestion-batch-id"),
            lineage_node_id=get("lineage-node-id"),
            schema_version=get("schema-version"),
            source_system=get("source-system"),
            source_reference=get("source-reference"),
            kms_key_id=get("kms-key-id"),
        )


@dataclass(frozen=True)
class StorageObject:
    """A stored object's descriptor (never the content itself)."""

    location: StorageLocation
    metadata: ObjectMetadata
    size_bytes: int
    version_id: str | None
    created_at: datetime
    content_type: str


@dataclass(frozen=True)
class StorageHealth:
    healthy: bool
    backend: str
    detail: str = ""


class StorageClient(ABC):
    """The single sanctioned interface for AequorOS object storage.

    Semantics every backend must honor (enforced by the contract suite):

    - Writes are idempotent for identical (location, checksum): re-writing
      the same content to the same location returns the existing object.
    - Every object is written with the full custom metadata; incomplete
      metadata raises :class:`StorageValidationError` before any bytes move.
    - Deletes are logical (delete marker) for retained tiers and physical
      only for ``temp``.
    - Every operation is recorded through the access log hook.
    """

    @abstractmethod
    def write(
        self,
        location: StorageLocation,
        data: BinaryIO,
        metadata: ObjectMetadata,
        content_type: str = "application/octet-stream",
    ) -> StorageObject: ...

    @abstractmethod
    def read(
        self,
        location: StorageLocation,
        version_id: str | None = None,
    ) -> tuple[StorageObject, BinaryIO]: ...

    @abstractmethod
    def exists(self, location: StorageLocation) -> bool: ...

    @abstractmethod
    def list(
        self,
        institution_slug: str,
        tier: Tier,
        prefix: str = "",
        limit: int | None = None,
    ) -> Iterator[StorageObject]: ...

    @abstractmethod
    def list_versions(self, location: StorageLocation) -> Iterator[StorageObject]: ...

    @abstractmethod
    def delete(self, location: StorageLocation) -> None: ...

    @abstractmethod
    def get_metadata(
        self,
        location: StorageLocation,
        version_id: str | None = None,
    ) -> ObjectMetadata: ...

    @abstractmethod
    def presigned_url(
        self,
        location: StorageLocation,
        operation: Literal["read", "write"],
        expires_in_seconds: int = 900,
    ) -> str: ...

    @abstractmethod
    def health_check(self) -> StorageHealth: ...

    def ensure_institution(self, institution_slug: str) -> None:
        """Idempotently provision the institution's buckets if the backend
        provisions in-band (MinIO). Cloud backends provision via Terraform
        (storage.md §3.3) and keep this a no-op."""
