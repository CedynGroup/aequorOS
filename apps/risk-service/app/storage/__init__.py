"""AequorOS storage layer (storage.md).

The single sanctioned surface for durable artifact storage beneath the Data
Engine. Application code depends on :class:`~app.storage.client.StorageClient`
only; the backing store (MinIO for MVP, GCS/S3 later) is a configuration
choice, never a code choice.
"""

from app.storage.client import (
    ObjectMetadata,
    StorageAccessError,
    StorageBackendError,
    StorageClient,
    StorageError,
    StorageLocation,
    StorageNotFoundError,
    StorageObject,
    StorageValidationError,
)

__all__ = [
    "ObjectMetadata",
    "StorageAccessError",
    "StorageBackendError",
    "StorageClient",
    "StorageError",
    "StorageLocation",
    "StorageNotFoundError",
    "StorageObject",
    "StorageValidationError",
]
