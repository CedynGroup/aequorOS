from __future__ import annotations

from typing import Protocol

from app.integrations.storage.base import ObjectStorage, PresignedUpload, StoredObjectHead


class MutableObjectStorage(ObjectStorage, Protocol):
    head: StoredObjectHead | None

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

    def delete_object(self, *, bucket: str, object_key: str) -> None: ...
