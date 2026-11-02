from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache
from typing import Any, Protocol, cast

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from app.core.config import get_settings
from app.core.content_digest import sha256_of_chunks
from app.core.tls import require_boto_tls, require_https
from app.integrations.storage.base import PresignedUpload, StoredObjectHead

_DIGEST_CHUNK_BYTES = 1 << 20


class _Body(Protocol):
    def read(self, size: int) -> bytes: ...

    def close(self) -> None: ...


class _ObjectClient(Protocol):
    """The slice of the boto3 client this adapter uses.

    Naming it keeps ``self._client`` off the untyped surface of ``boto3.client``,
    so the adapter's own code does not have to treat every response as unknown.
    """

    def generate_presigned_url(
        self, operation: str, *, Params: dict[str, str], ExpiresIn: int, HttpMethod: str
    ) -> str: ...

    def head_object(self, *, Bucket: str, Key: str) -> dict[str, Any]: ...

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, Any]: ...

    def delete_object(self, *, Bucket: str, Key: str) -> dict[str, Any]: ...


def _http_status(error: ClientError) -> int | None:
    metadata = cast(dict[str, Any], error.response).get("ResponseMetadata")
    if not isinstance(metadata, dict):
        return None
    status_code = cast(dict[str, object], metadata).get("HTTPStatusCode")
    return status_code if isinstance(status_code, int) else None


def _body_chunks(body: _Body) -> Iterator[bytes]:
    while chunk := body.read(_DIGEST_CHUNK_BYTES):
        yield chunk


class S3ObjectStorage:
    def __init__(
        self,
        *,
        region_name: str,
        endpoint_url: str | None,
        access_key_id: str | None,
        secret_access_key: str | None,
        force_path_style: bool,
    ) -> None:
        if endpoint_url:
            require_https(endpoint_url, field="S3_ENDPOINT")
        config = Config(
            signature_version="s3v4",
            s3={"addressing_style": "path" if force_path_style else "virtual"},
        )
        self._client: _ObjectClient = cast(
            _ObjectClient,
            cast(
                object,
                boto3.client(
                    "s3",
                    verify=get_settings().tls.ca_bundle or True,
                    use_ssl=True,
                    region_name=region_name,
                    endpoint_url=endpoint_url or None,
                    aws_access_key_id=access_key_id,
                    aws_secret_access_key=secret_access_key,
                    config=config,
                ),
            ),
        )

        require_boto_tls(cast(object, self._client))

    def create_presigned_upload_url(
        self,
        *,
        bucket: str,
        object_key: str,
        content_type: str,
        expires_seconds: int,
    ) -> PresignedUpload:
        url = self._client.generate_presigned_url(
            "put_object",
            Params={"Bucket": bucket, "Key": object_key, "ContentType": content_type},
            ExpiresIn=expires_seconds,
            HttpMethod="PUT",
        )
        return PresignedUpload(
            url=url,
            method="PUT",
            headers={"Content-Type": content_type},
            expires_in_seconds=expires_seconds,
        )

    def create_presigned_download_url(
        self,
        *,
        bucket: str,
        object_key: str,
        expires_seconds: int,
    ) -> str:
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": object_key},
            ExpiresIn=expires_seconds,
            HttpMethod="GET",
        )

    def head_object(self, *, bucket: str, object_key: str) -> StoredObjectHead | None:
        try:
            response = self._client.head_object(Bucket=bucket, Key=object_key)
        except ClientError as exc:
            if _http_status(exc) == 404:
                return None
            raise
        return StoredObjectHead(
            content_type=cast(str | None, response.get("ContentType")),
            byte_size=cast(int | None, response.get("ContentLength")),
            etag=cast(str | None, response.get("ETag")),
            version_id=cast(str | None, response.get("VersionId")),
        )

    def read_object_digest(
        self, *, bucket: str, object_key: str, max_bytes: int
    ) -> tuple[int, str] | None:
        """Hash the object as it is stored, refusing to read past ``max_bytes``."""
        try:
            response = self._client.get_object(Bucket=bucket, Key=object_key)
        except ClientError as exc:
            if _http_status(exc) == 404:
                return None
            raise
        body = cast(_Body, cast(object, response["Body"]))
        try:
            return sha256_of_chunks(_body_chunks(body), max_bytes=max_bytes)
        finally:
            body.close()

    def delete_object(self, *, bucket: str, object_key: str) -> None:
        _ = self._client.delete_object(Bucket=bucket, Key=object_key)


@lru_cache
def get_object_storage() -> S3ObjectStorage:
    settings = get_settings()
    return S3ObjectStorage(
        region_name=settings.risk_s3_region,
        endpoint_url=settings.risk_s3_endpoint_url,
        access_key_id=settings.risk_s3_access_key_id,
        secret_access_key=settings.risk_s3_secret_access_key,
        force_path_style=settings.risk_s3_force_path_style,
    )
