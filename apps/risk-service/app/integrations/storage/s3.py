from __future__ import annotations

from functools import lru_cache
from typing import Any

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from app.core.config import get_settings
from app.integrations.storage.base import PresignedUpload, StoredObjectHead


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
        config = Config(
            signature_version="s3v4",
            s3={"addressing_style": "path" if force_path_style else "virtual"},
        )
        self._client = boto3.client(
            "s3",
            region_name=region_name,
            endpoint_url=endpoint_url or None,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            config=config,
        )

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
            response: dict[str, Any] = self._client.head_object(Bucket=bucket, Key=object_key)
        except ClientError as exc:
            status_code = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if status_code == 404:
                return None
            raise
        return StoredObjectHead(
            content_type=response.get("ContentType"),
            byte_size=response.get("ContentLength"),
            etag=response.get("ETag"),
            version_id=response.get("VersionId"),
        )

    def delete_object(self, *, bucket: str, object_key: str) -> None:
        self._client.delete_object(Bucket=bucket, Key=object_key)


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
