"""S3-protocol StorageClient backend serving MinIO (MVP) and Amazon S3.

MinIO speaks the S3 wire protocol, so one boto3-based implementation covers
both ``backend: minio`` and ``backend: s3`` — a deliberate simplification of
storage.md §5, which describes them separately. The parity matrix still
applies: behavior differences (Object Lock, KES vs SSE-KMS) are handled here
and never leak through the interface. GCS lands later as a true second
implementation against the same contract suite.

Encryption note: KES went live on the MVP MinIO on 2026-07-15 with the
platform key ``aequoros-key``. Every write is SSE-KMS encrypted under the
configured ``STORAGE_KMS_KEY_ID`` unless the caller names a different key in
metadata, and provisioning sets the same key as each bucket's default so
presigned uploads inherit it. One platform key for all institutions is a
tracked deviation from §7.2 (one key per institution) until KES key creation
is automated in onboarding.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, BinaryIO, Literal

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

from app.storage.access_log import AccessLogHook, HashChainedAccessLog, null_access_log
from app.storage.client import (
    RETAINED_TIERS,
    ObjectMetadata,
    StorageAccessError,
    StorageBackendError,
    StorageClient,
    StorageEnv,
    StorageError,
    StorageHealth,
    StorageLocation,
    StorageNotFoundError,
    StorageObject,
    StorageValidationError,
    Tier,
)
from app.storage.config import StorageEngineSettings, enforce_retirement
from app.storage.provisioning import ensure_audit_bucket, provision_institution

logger = logging.getLogger(__name__)

_ACCESS_DENIED_CODES = {"AccessDenied", "InvalidAccessKeyId", "SignatureDoesNotMatch", "403"}
_NOT_FOUND_CODES = {"NoSuchKey", "NoSuchBucket", "NoSuchVersion", "404", "NotFound"}


class S3CompatibleStorageClient(StorageClient):
    def __init__(
        self,
        settings: StorageEngineSettings,
        *,
        access_log: AccessLogHook | None = None,
        client_factory: Callable[..., Any] | None = None,
    ) -> None:
        enforce_retirement(settings)
        if not settings.configured:
            msg = "Storage endpoint and credentials are not configured."
            raise StorageValidationError(msg)
        self._settings = settings
        self._env: StorageEnv = settings.env
        self._log = access_log or null_access_log
        factory = client_factory or boto3.client
        self._s3 = factory(
            "s3",
            endpoint_url=settings.endpoint,
            aws_access_key_id=settings.access_key,
            aws_secret_access_key=settings.secret_key,
            region_name=settings.region,
            config=BotoConfig(
                s3={"addressing_style": "path" if settings.force_path_style else "auto"},
                retries={"max_attempts": 5, "mode": "adaptive"},
            ),
        )

    # -- contract operations ------------------------------------------------

    def write(
        self,
        location: StorageLocation,
        data: BinaryIO,
        metadata: ObjectMetadata,
        content_type: str = "application/octet-stream",
    ) -> StorageObject:
        self._validate_metadata(location, metadata)
        bucket = location.bucket_name(self._env)

        existing = self._stat_or_none(bucket, location.object_path)
        if existing is not None:
            existing_metadata = {
                key.lower(): value for key, value in existing.get("Metadata", {}).items()
            }
            if existing_metadata.get("checksum-sha256") == metadata.checksum_sha256:
                self._log("write.noop", location, version_id=existing.get("VersionId"))
                return self._to_storage_object(location, existing)

        effective_key = metadata.kms_key_id or self._settings.kms_key_id
        if effective_key is not None and metadata.kms_key_id is None:
            metadata = replace(metadata, kms_key_id=effective_key)
        put_kwargs: dict[str, Any] = {
            "Bucket": bucket,
            "Key": location.object_path,
            "Body": data,
            "ContentType": content_type,
            "Metadata": metadata.to_object_metadata(),
        }
        if effective_key is not None:
            put_kwargs["ServerSideEncryption"] = "aws:kms"
            put_kwargs["SSEKMSKeyId"] = effective_key
        response = self._call("write", location, lambda: self._s3.put_object(**put_kwargs))
        stat = self._call(
            "write.stat",
            location,
            lambda: self._stat_object(bucket, location.object_path),
            log=False,
        )
        self._log("write", location, version_id=response.get("VersionId"))
        return self._to_storage_object(location, stat, version_id=response.get("VersionId"))

    def read(
        self,
        location: StorageLocation,
        version_id: str | None = None,
    ) -> tuple[StorageObject, BinaryIO]:
        bucket = location.bucket_name(self._env)
        kwargs: dict[str, Any] = {"Bucket": bucket, "Key": location.object_path}
        if version_id is not None:
            kwargs["VersionId"] = version_id
        response = self._call("read", location, lambda: self._s3.get_object(**kwargs))
        self._log("read", location, version_id=response.get("VersionId"))
        return self._to_storage_object(location, response), response["Body"]

    def exists(self, location: StorageLocation) -> bool:
        stat = self._stat_or_none(location.bucket_name(self._env), location.object_path)
        self._log("exists", location, result="found" if stat else "missing")
        return stat is not None

    def list(
        self,
        institution_slug: str,
        tier: Tier,
        prefix: str = "",
        limit: int | None = None,
    ) -> Iterator[StorageObject]:
        location = StorageLocation(institution_slug=institution_slug, tier=tier, object_path=prefix)
        bucket = location.bucket_name(self._env)
        self._log("list", location)
        paginator = self._s3.get_paginator("list_objects_v2")
        yielded = 0
        try:
            for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
                for entry in page.get("Contents", []):
                    if limit is not None and yielded >= limit:
                        return
                    object_location = StorageLocation(
                        institution_slug=institution_slug,
                        tier=tier,
                        object_path=entry["Key"],
                    )
                    stat = self._call(
                        "list.stat",
                        object_location,
                        lambda key=entry["Key"]: self._stat_object(bucket, key),
                        log=False,
                    )
                    yield self._to_storage_object(object_location, stat)
                    yielded += 1
        except ClientError as exc:
            raise self._translate(exc) from exc

    def list_versions(self, location: StorageLocation) -> Iterator[StorageObject]:
        bucket = location.bucket_name(self._env)
        self._log("list_versions", location)
        paginator = self._s3.get_paginator("list_object_versions")
        try:
            for page in paginator.paginate(Bucket=bucket, Prefix=location.object_path):
                for entry in page.get("Versions", []):
                    if entry["Key"] != location.object_path:
                        continue
                    stat = self._call(
                        "list_versions.stat",
                        location,
                        lambda version=entry["VersionId"]: self._stat_object(
                            bucket, location.object_path, version_id=version
                        ),
                        log=False,
                    )
                    yield self._to_storage_object(location, stat, version_id=entry["VersionId"])
        except ClientError as exc:
            raise self._translate(exc) from exc

    def delete(self, location: StorageLocation) -> None:
        bucket = location.bucket_name(self._env)
        if location.tier in RETAINED_TIERS:
            # Versioned bucket: a bare delete writes a delete marker; content
            # stays retrievable via list_versions until lifecycle expiry.
            self._call(
                "delete.logical",
                location,
                lambda: self._s3.delete_object(Bucket=bucket, Key=location.object_path),
            )
            self._log("delete.logical", location)
            return
        versions = self._call(
            "delete.list",
            location,
            lambda: self._s3.list_object_versions(Bucket=bucket, Prefix=location.object_path),
            log=False,
        )
        for entry in versions.get("Versions", []) + versions.get("DeleteMarkers", []):
            if entry["Key"] != location.object_path:
                continue
            self._call(
                "delete.physical",
                location,
                lambda version=entry["VersionId"]: self._s3.delete_object(
                    Bucket=bucket, Key=location.object_path, VersionId=version
                ),
                log=False,
            )
        self._log("delete.physical", location)

    def get_metadata(
        self,
        location: StorageLocation,
        version_id: str | None = None,
    ) -> ObjectMetadata:
        bucket = location.bucket_name(self._env)
        kwargs: dict[str, Any] = {"Bucket": bucket, "Key": location.object_path}
        if version_id is not None:
            kwargs["VersionId"] = version_id
        stat = self._call(
            "get_metadata",
            location,
            lambda: self._stat_object(bucket, location.object_path, version_id=version_id),
        )
        self._log("get_metadata", location, version_id=stat.get("VersionId"))
        return ObjectMetadata.from_object_metadata(stat.get("Metadata", {}))

    def presigned_url(
        self,
        location: StorageLocation,
        operation: Literal["read", "write"],
        expires_in_seconds: int = 900,
    ) -> str:
        bucket = location.bucket_name(self._env)
        method = "get_object" if operation == "read" else "put_object"
        url = self._call(
            "presigned_url",
            location,
            lambda: self._s3.generate_presigned_url(
                method,
                Params={"Bucket": bucket, "Key": location.object_path},
                ExpiresIn=expires_in_seconds,
            ),
        )
        self._log(f"presigned_url.{operation}", location)
        return url

    def health_check(self) -> StorageHealth:
        try:
            self._s3.list_buckets()
        except (ClientError, BotoCoreError) as exc:
            return StorageHealth(healthy=False, backend=self._settings.backend, detail=str(exc))
        return StorageHealth(healthy=True, backend=self._settings.backend)

    def ensure_institution(self, institution_slug: str) -> None:
        provision_institution(self._s3, self._settings, institution_slug)

    def flush_access_log(self) -> str | None:
        if not isinstance(self._log, HashChainedAccessLog):
            return None
        segment = self._log.drain_segment()
        if segment is None:
            return None
        jsonl, first, last = segment
        bucket = f"aequoros-{self._env}-audit-logs"
        ensure_audit_bucket(self._s3, self._settings, bucket)
        stamp = datetime.now(UTC)
        key = (
            f"{self._log.identity}/{stamp:%Y-%m-%d}/{stamp:%H%M%S}-seq{first:08d}-{last:08d}.jsonl"
        )
        put_kwargs: dict[str, Any] = {
            "Bucket": bucket,
            "Key": key,
            "Body": jsonl.encode(),
            "ContentType": "application/jsonl",
        }
        if self._settings.kms_key_id is not None:
            put_kwargs["ServerSideEncryption"] = "aws:kms"
            put_kwargs["SSEKMSKeyId"] = self._settings.kms_key_id
        try:
            self._s3.put_object(**put_kwargs)
        except (ClientError, BotoCoreError) as exc:
            # Fail loudly in logs but never mask the operation being audited.
            logger.error("access-log segment flush failed: %s", exc)
            return None
        return f"{bucket}/{key}"

    # -- internals ------------------------------------------------------------

    def _validate_metadata(self, location: StorageLocation, metadata: ObjectMetadata) -> None:
        problems: list[str] = []
        if not metadata.checksum_sha256:
            problems.append("checksum_sha256 is required")
        if not metadata.written_by:
            problems.append("written_by is required")
        if metadata.institution_slug != location.institution_slug:
            problems.append("metadata institution_slug does not match location")
        if metadata.tier != location.tier:
            problems.append("metadata tier does not match location")
        if metadata.lineage_node_id is None:
            # Production treats missing lineage as an error; dev/mvp warns
            # loudly instead so early integration gaps stay visible (§14.7).
            if self._env == "prod":
                problems.append("lineage_node_id is required in production")
            else:
                logger.warning(
                    "storage write without lineage_node_id: %s/%s/%s",
                    location.institution_slug,
                    location.tier,
                    location.object_path,
                )
        if problems:
            raise StorageValidationError("; ".join(problems))

    def _stat_or_none(self, bucket: str, key: str) -> dict[str, Any] | None:
        try:
            return self._stat_object(bucket, key)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in _NOT_FOUND_CODES:
                return None
            raise self._translate(exc) from exc

    def _stat_object(self, bucket: str, key: str, version_id: str | None = None) -> dict[str, Any]:
        """Object descriptor via HEAD, falling back to GET behind broken proxies.

        The MVP MinIO sits behind a Cloudflare WAF that 403s HEAD requests for
        several file extensions (.csv, .xlsx, .bin — probed 2026-07-14) and
        mangles ranged GETs into signature failures, while plain GET and PUT
        pass. HEAD is tried first (correct everywhere, and the only path used
        on AWS/plain MinIO); on a 403 the client falls back to a plain GET and
        closes the stream as soon as the headers arrive. Remove the fallback
        once the Cloudflare rule for s3.cedynhq.com is fixed.
        """
        kwargs: dict[str, Any] = {"Bucket": bucket, "Key": key}
        if version_id is not None:
            kwargs["VersionId"] = version_id
        try:
            return self._s3.head_object(**kwargs)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code", "") not in _ACCESS_DENIED_CODES:
                raise
        response = self._s3.get_object(**kwargs)
        response["Body"].close()
        return response

    def _call(
        self,
        operation: str,
        location: StorageLocation,
        fn: Callable[[], Any],
        *,
        log: bool = True,
    ) -> Any:
        try:
            return fn()
        except ClientError as exc:
            if log:
                self._log(operation, location, result=exc.response["Error"].get("Code", "error"))
            raise self._translate(exc) from exc
        except BotoCoreError as exc:
            if log:
                self._log(operation, location, result="backend_error")
            raise StorageBackendError(str(exc)) from exc

    @staticmethod
    def _translate(exc: ClientError) -> StorageError:
        code = exc.response.get("Error", {}).get("Code", "")
        message = exc.response.get("Error", {}).get("Message", str(exc))
        if code in _NOT_FOUND_CODES:
            return StorageNotFoundError(message)
        if code in _ACCESS_DENIED_CODES:
            return StorageAccessError(message)
        return StorageBackendError(f"{code}: {message}")

    def _to_storage_object(
        self,
        location: StorageLocation,
        response: dict[str, Any],
        *,
        version_id: str | None = None,
    ) -> StorageObject:
        raw_metadata = response.get("Metadata", {})
        metadata = ObjectMetadata.from_object_metadata(raw_metadata)
        return StorageObject(
            location=location,
            metadata=metadata,
            size_bytes=response.get("ContentLength", 0),
            version_id=version_id or response.get("VersionId"),
            created_at=response.get("LastModified", datetime.now(UTC)),
            content_type=response.get("ContentType", "application/octet-stream"),
        )
