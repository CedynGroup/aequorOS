"""Conditional replacement of editable objects; retained version IDs never change."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Generator, Iterator
from contextlib import AbstractContextManager, contextmanager, nullcontext, suppress
from datetime import UTC, datetime
from tempfile import SpooledTemporaryFile
from typing import BinaryIO, Protocol, cast
from uuid import UUID

# pyright: reportMissingTypeStubs=false
from botocore.exceptions import BotoCoreError, ClientError
from pydantic import BaseModel, Field, TypeAdapter

from app.storage.client import (
    TIERS,
    EncryptionCursor,
    EncryptionInventoryItem,
    EncryptionPage,
    ObjectMetadata,
    ReencryptionIsolation,
    StorageAccessError,
    StorageBackendError,
    StorageLocation,
    StorageValidationError,
)
from app.storage.encryption import ENCRYPTION_FORMAT, ObjectEncryption

MULTIPART_SIZE = 16 * 1024 * 1024


class S3MaintenanceClient(Protocol):
    def list_object_versions(self, **kwargs: object) -> dict[str, object]: ...
    def get_object(self, **kwargs: object) -> dict[str, object]: ...
    def put_object(self, **kwargs: object) -> dict[str, object]: ...
    def create_multipart_upload(self, **kwargs: object) -> dict[str, object]: ...
    def upload_part(self, **kwargs: object) -> dict[str, object]: ...
    def complete_multipart_upload(self, **kwargs: object) -> dict[str, object]: ...
    def abort_multipart_upload(self, **kwargs: object) -> dict[str, object]: ...


class ObjectStat(Protocol):
    def __call__(
        self, bucket: str, key: str, version_id: str | None = None
    ) -> dict[str, object]: ...


class _Version(BaseModel):
    Key: str
    VersionId: str
    IsLatest: bool


class _Page(BaseModel):
    Versions: list[_Version] = Field(default_factory=list)
    IsTruncated: bool = False
    NextKeyMarker: str | None = None
    NextVersionIdMarker: str | None = None


_HEADERS = TypeAdapter(dict[str, str])


def _metadata(response: dict[str, object]) -> dict[str, str]:
    return {
        key.lower(): value
        for key, value in _HEADERS.validate_python(response.get("Metadata", {})).items()
    }


def _locked(stat: dict[str, object]) -> bool:
    if stat.get("ObjectLockLegalHoldStatus") == "ON":
        return True
    if not stat.get("ObjectLockMode"):
        return False
    until = stat.get("ObjectLockRetainUntilDate")
    if not isinstance(until, datetime):
        raise StorageBackendError("Object retention deadline could not be verified.")
    if until.tzinfo is None:
        until = until.replace(tzinfo=UTC)
    return until > datetime.now(UTC)


def inventory(
    client: S3MaintenanceClient, env: str, slug: str, stat_object: ObjectStat
) -> Iterator[EncryptionInventoryItem]:
    cursor: EncryptionCursor | None = EncryptionCursor()
    while cursor is not None:
        result = inventory_page(client, env, slug, stat_object, cursor, 100)
        yield from result.items
        cursor = result.cursor


def inventory_page(  # noqa: PLR0913
    client: S3MaintenanceClient,
    env: str,
    slug: str,
    stat_object: ObjectStat,
    cursor: EncryptionCursor,
    limit: int,
) -> EncryptionPage:
    try:
        tier = TIERS[cursor.tier]
        bucket = StorageLocation(slug, tier, "").bucket_name(env)
        arguments: dict[str, object] = {"Bucket": bucket, "MaxKeys": limit}
        if cursor.key is not None:
            arguments["KeyMarker"] = cursor.key
        if cursor.version is not None:
            arguments["VersionIdMarker"] = cursor.version
        page = _Page.model_validate(client.list_object_versions(**arguments))
        items: list[EncryptionInventoryItem] = []
        for entry in page.Versions:
            location = StorageLocation(slug, tier, entry.Key)
            stat = stat_object(bucket, entry.Key, version_id=entry.VersionId)
            headers = _metadata(stat)
            envelope = headers.get("key-envelope-id")
            format_id = headers.get("encryption-format")
            if bool(format_id) != bool(envelope) or format_id and format_id != ENCRYPTION_FORMAT:
                raise StorageAccessError("Object encryption metadata is invalid.")
            items.append(
                EncryptionInventoryItem(
                    location,
                    entry.VersionId,
                    UUID(envelope) if envelope else None,
                    not entry.IsLatest or _locked(stat),
                    TypeAdapter(int).validate_python(stat["ContentLength"]),
                )
            )
        if page.IsTruncated:
            if page.NextKeyMarker is None:
                raise StorageBackendError("Object inventory cursor is unavailable.")
            following = EncryptionCursor(cursor.tier, page.NextKeyMarker, page.NextVersionIdMarker)
        else:
            following = EncryptionCursor(cursor.tier + 1) if cursor.tier + 1 < len(TIERS) else None
        return EncryptionPage(items, following)
    except (ClientError, BotoCoreError, ValueError) as exc:
        raise StorageBackendError("Object encryption inventory could not be verified.") from exc


def _legacy_plaintext(body: BinaryIO, checksum: str) -> BinaryIO:
    output = SpooledTemporaryFile(max_size=8 * 1024 * 1024)  # noqa: SIM115 - caller owns result
    digest = hashlib.sha256()
    try:
        for chunk in iter(lambda: body.read(1024 * 1024), b""):
            digest.update(chunk)
            _ = output.write(chunk)
        if digest.hexdigest() != checksum:
            raise StorageAccessError("Legacy object checksum could not be verified.")
        _ = output.seek(0)
        return cast(BinaryIO, output)
    except BaseException:
        output.close()
        raise
    finally:
        body.close()


def _publish(
    client: S3MaintenanceClient,
    data: BinaryIO,
    arguments: dict[str, object],
    publication: Callable[[], AbstractContextManager[bool]] | None = None,
) -> bool:
    size = data.seek(0, 2)
    _ = data.seek(0)
    if size < MULTIPART_SIZE and publication is None:
        _ = client.put_object(**arguments, Body=data)
        return True
    options = {key: value for key, value in arguments.items() if key != "IfMatch"}
    upload = client.create_multipart_upload(**options)
    location = {key: arguments[key] for key in ("Bucket", "Key")}
    location["UploadId"] = upload["UploadId"]
    parts: list[dict[str, object]] = []
    try:
        for number, body in enumerate(iter(lambda: data.read(MULTIPART_SIZE), b""), start=1):
            response = client.upload_part(**location, PartNumber=number, Body=body)
            parts.append({"ETag": response["ETag"], "PartNumber": number})
        with publication() if publication else nullcontext(True) as allowed:
            if not allowed:
                _ = client.abort_multipart_upload(**location)
                return False
            _ = client.complete_multipart_upload(
                **location, MultipartUpload={"Parts": parts}, IfMatch=arguments["IfMatch"]
            )
        return True
    except BaseException:
        with suppress(ClientError, BotoCoreError):
            _ = client.abort_multipart_upload(**location)
        raise


def reencrypt(  # noqa: PLR0913 - storage boundary carries encryption context
    client: S3MaintenanceClient,
    env: str,
    encryption: ObjectEncryption,
    item: EncryptionInventoryItem,
    sse_key: str | None,
    stat_object: ObjectStat,
    *,
    isolation: ReencryptionIsolation | None = None,
) -> bool:
    if item.retained:
        raise StorageValidationError("Retained object versions cannot be rewritten.")
    location = item.location
    with encryption.publication(location) as unit:
        bucket = location.bucket_name(env)
        body: BinaryIO | None = None
        try:
            stat = stat_object(bucket, location.object_path)
            if str(stat.get("VersionId", "null")) != item.version_id or _locked(stat):
                return False
            source = client.get_object(
                Bucket=bucket,
                Key=location.object_path,
                VersionId=item.version_id,
            )
            body = cast(BinaryIO, source["Body"])
            headers = _metadata(source)
            if headers.get("key-envelope-id") != (
                str(item.envelope_id) if item.envelope_id is not None else None
            ):
                return False
            metadata = ObjectMetadata.from_object_metadata(headers)
            source_headers = dict(headers)
            if isolation is not None:
                if cast(int, source["ContentLength"]) != item.size_bytes:
                    return False
                unit = unit.detached(location, metadata, item.envelope_id)
                isolation.release()
            plaintext = (
                unit.decrypt(location, body, metadata, headers)
                if headers.get("encryption-format")
                else _legacy_plaintext(body, metadata.checksum_sha256)
            )
            try:
                encrypted, envelope, key_id = unit.encrypt(location, plaintext, metadata)
            finally:
                plaintext.close()
            try:
                if envelope is None:
                    raise StorageAccessError("Re-encryption requires a connected bank key.")
                target_version = UUID(envelope)
                headers.update(
                    {
                        "encryption-format": ENCRYPTION_FORMAT,
                        "key-envelope-id": envelope,
                        "kms-key-id": key_id or "",
                    }
                )
                arguments: dict[str, object] = {
                    "Bucket": bucket,
                    "Key": location.object_path,
                    "Metadata": headers,
                    "ContentType": source.get("ContentType", "application/octet-stream"),
                    "IfMatch": source["ETag"],
                }
                if sse_key:
                    arguments.update(ServerSideEncryption="aws:kms", SSEKMSKeyId=sse_key)

                @contextmanager
                def publication() -> Generator[bool]:
                    assert isolation is not None
                    with isolation.publication(target_version) as allowed:
                        if not allowed:
                            yield False
                            return
                        latest = stat_object(bucket, location.object_path)
                        yield (
                            str(latest.get("VersionId", "null")) == item.version_id
                            and latest.get("ETag") == source["ETag"]
                            and _metadata(latest) == source_headers
                            and not _locked(latest)
                        )

                return _publish(client, encrypted, arguments, publication if isolation else None)
            finally:
                encrypted.close()
        except ClientError as exc:
            response = TypeAdapter(dict[str, object]).validate_python(exc.response)
            error = TypeAdapter(dict[str, object]).validate_python(response.get("Error", {}))
            if error.get("Code") in {
                "PreconditionFailed",
                "ConditionalRequestConflict",
                "NoSuchKey",
                "NoSuchVersion",
                "404",
            }:
                return False
            raise StorageBackendError("Object re-encryption could not be completed.") from exc
        except BotoCoreError as exc:
            raise StorageBackendError("Object re-encryption could not be completed.") from exc
        finally:
            if body is not None:
                body.close()
