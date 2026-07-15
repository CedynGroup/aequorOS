"""Process-wide StorageClient construction (storage.md §4.4).

Backend selection happens here, once, from configuration. Application code
imports :func:`get_storage_client` and never a concrete backend.
"""

from __future__ import annotations

from functools import lru_cache

from app.storage.access_log import HashChainedAccessLog
from app.storage.client import StorageClient
from app.storage.config import get_storage_settings
from app.storage.s3_compatible import S3CompatibleStorageClient


@lru_cache
def get_storage_client() -> StorageClient:
    settings = get_storage_settings()
    access_log = HashChainedAccessLog(identity="risk-service")
    if settings.backend in ("minio", "s3"):
        return S3CompatibleStorageClient(settings, access_log=access_log)
    msg = f"Storage backend {settings.backend!r} is not implemented yet (Phase 2)."
    raise NotImplementedError(msg)
