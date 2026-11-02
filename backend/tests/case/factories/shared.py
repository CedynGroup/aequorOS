from __future__ import annotations

from typing import Protocol

from app.integrations.storage.base import ObjectStorage, PresignedUpload, StoredObjectHead


class MutableObjectStorage(ObjectStorage, Protocol):
    head: StoredObjectHead | None
    body: bytes
    digest_reads: list[tuple[str, str]]
    finalized_objects: dict[tuple[str, str], tuple[StoredObjectHead, bytes]]
    downloads: list[tuple[str, str]]

    def create_presigned_upload_url(
        self,
        *,
        bucket: str,
        object_key: str,
        content_type: str,
        expires_seconds: int,
    ) -> PresignedUpload: ...

    def create_presigned_download_url(
        self,
        *,
        bucket: str,
        object_key: str,
        expires_seconds: int,
    ) -> str: ...

    def head_object(self, *, bucket: str, object_key: str) -> StoredObjectHead | None: ...

    def read_object_digest(
        self, *, bucket: str, object_key: str, max_bytes: int
    ) -> tuple[int, str] | None: ...

    def delete_object(self, *, bucket: str, object_key: str) -> None: ...
