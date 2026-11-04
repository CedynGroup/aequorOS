from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class PresignedUpload:
    url: str
    method: str
    headers: dict[str, str]
    expires_in_seconds: int


@dataclass(frozen=True)
class StoredObjectHead:
    content_type: str | None = None
    byte_size: int | None = None
    etag: str | None = None
    version_id: str | None = None


class ObjectStorage(Protocol):
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

    def finalize_upload(self, *, bucket: str, object_key: str) -> str | None: ...

    def read_object_digest(
        self, *, bucket: str, object_key: str, max_bytes: int
    ) -> tuple[int, str] | None: ...

    def delete_object(self, *, bucket: str, object_key: str) -> None: ...
