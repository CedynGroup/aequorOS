"""S3-protocol StorageClient backend serving MinIO (MVP) and Amazon S3.

MinIO speaks the S3 wire protocol, so one boto3-based implementation covers
both ``backend: minio`` and ``backend: s3`` — a deliberate simplification of
storage.md §5, which describes them separately. The parity matrix still
applies: behavior differences (Object Lock, KES vs SSE-KMS) are handled here
and never leak through the interface. GCS lands later as a true second
implementation against the same contract suite.

Objects of banks with connected keys use AWS Encryption SDK messages.
Their key hierarchy follows ``backend/docs/bank_encryption.md``.
During optional rollout, banks without keys retain platform storage encryption.
Bucket SSE also protects platform audit logs.

"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable, Iterator, Mapping
from contextlib import suppress
from copy import copy
from dataclasses import replace
from datetime import UTC, datetime
from tempfile import SpooledTemporaryFile
from typing import Any, BinaryIO, Literal, Protocol, Self, cast
from uuid import UUID

import boto3
from boto3.exceptions import S3UploadFailedError
from boto3.s3.transfer import TransferConfig
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError
from pydantic import TypeAdapter, ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.key_management.types import KeyUnavailableError
from app.core.tls import require_boto_tls, require_https
from app.storage import reencryption
from app.storage.access_log import AccessLogHook, HashChainedAccessLog, null_access_log
from app.storage.client import (
    RETAINED_TIERS,
    EncryptionCursor,
    EncryptionInventoryItem,
    EncryptionPage,
    ObjectMetadata,
    ReencryptionIsolation,
    SealedEncryptionContext,
    SealedPublication,
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
    is_sealed_object_key,
    refuse_sealed_maintenance,
)
from app.storage.config import StorageEngineSettings, enforce_retirement
from app.storage.downloads import issue
from app.storage.encryption import ENCRYPTION_FORMAT, ObjectEncryption
from app.storage.provisioning import ProvisioningClient, ensure_audit_bucket, provision_institution
from app.storage.retention import RetentionClient, require_object_lock, retain_version

logger = logging.getLogger(__name__)
_OBJECT_HEADERS = TypeAdapter(dict[str, str])


class _S3ControlClient(Protocol):
    def generate_presigned_url(
        self, operation: str, *, Params: dict[str, str], ExpiresIn: int
    ) -> str: ...

    def list_buckets(self) -> object: ...


class _S3CreateClient(Protocol):
    """The create-only object operations, typed so a sealed write carries no ``Any``.

    boto3's own signatures are only partially known to the type checker, so the
    sealed path names the six calls it makes and reads their responses as
    mappings. The runtime client is the same object.
    """

    def put_object(self, **kwargs: object) -> Mapping[str, object]: ...

    def create_multipart_upload(self, **kwargs: object) -> Mapping[str, object]: ...

    def upload_part(self, **kwargs: object) -> Mapping[str, object]: ...

    def complete_multipart_upload(self, **kwargs: object) -> Mapping[str, object]: ...

    def abort_multipart_upload(self, **kwargs: object) -> Mapping[str, object]: ...


_ACCESS_DENIED_CODES = {"AccessDenied", "InvalidAccessKeyId", "SignatureDoesNotMatch", "403"}
_NOT_FOUND_CODES = {"NoSuchKey", "NoSuchBucket", "NoSuchVersion", "404", "NotFound"}
_PRECONDITION_FAILED_CODES = {"PreconditionFailed", "412"}
# A conditional write that collides with a competing write in flight. S3
# documents this as retryable; it is NOT the "the key already exists" answer.
_CONDITIONAL_CONFLICT_CODES = {"ConditionalRequestConflict", "409"}
_SEALED_CREATE_ATTEMPTS = 4


def _recorded_metadata(stat: Mapping[str, object]) -> dict[str, str]:
    """An object's recorded metadata, keyed as S3 returns it on HEAD.

    S3 lower-cases metadata names on the way in and back out, so a comparison
    against a header name has to be done in that case rather than the caller's.
    """
    raw = stat.get("Metadata")
    if not isinstance(raw, Mapping):
        return {}
    recorded = cast(Mapping[object, object], raw)
    return {str(key).lower(): str(value) for key, value in recorded.items()}


def _error_code(exc: ClientError) -> str:
    """The provider's own error code, which is what every branch here keys on."""
    response = cast(Mapping[str, object], exc.response)
    error = response.get("Error")
    if not isinstance(error, Mapping):
        return ""
    code = cast(Mapping[str, object], error).get("Code")
    return "" if code is None else str(code)


def _verified_payload(data: BinaryIO, checksum_sha256: str) -> BinaryIO:
    """Spool the payload and prove it is the content the catalogue will record.

    Sealed publication hashes the bytes it publishes rather than trusting the
    caller's own claim, so a mislabelled write cannot pin a checksum the object
    does not have.
    """
    output = SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b")  # noqa: SIM115 - caller owns result
    digest = hashlib.sha256()
    try:
        for chunk in iter(lambda: data.read(1024 * 1024), b""):
            digest.update(chunk)
            _ = output.write(chunk)
        if digest.hexdigest() != checksum_sha256:
            raise StorageValidationError(
                "Sealed publication content does not match its recorded checksum."
            )
        _ = output.seek(0)
        return cast(BinaryIO, output)
    except BaseException:
        output.close()
        raise
    finally:
        data.close()


# A staged bundle from a core-banking-scale pull (hundreds of thousands of rows
# serialized into one object) can be hundreds of MB — larger than the single
# request body a backend/proxy accepts (MinIO behind Cloudflare 413s a >100 MB
# PutObject). Above the threshold, boto3's managed transfer switches to a
# multipart upload, sending parts each well under that limit; MinIO requires
# every part except the last to be >= 5 MiB, so the chunk size stays comfortably
# above that. This makes writes size-independent — the correct fix, not a cap on
# how much data a bank can load.
_UPLOAD_TRANSFER_CONFIG = TransferConfig(
    multipart_threshold=reencryption.MULTIPART_SIZE,
    multipart_chunksize=reencryption.MULTIPART_SIZE,
    use_threads=True,
)


class S3CompatibleStorageClient(StorageClient):
    def __init__(
        self,
        settings: StorageEngineSettings,
        *,
        access_log: AccessLogHook | None = None,
        encryption: ObjectEncryption | None = None,
        client_factory: Callable[..., Any] | None = None,
    ) -> None:
        # Order matters. A deployment that does not exist cannot be past its
        # retirement date, so "not configured" is the accurate answer and has to
        # come first. Retiring first told a caller with NO storage at all that
        # its nonexistent MinIO lacked a retirement date — which is how a test
        # missing its in-memory fixture surfaced as a retirement-policy failure
        # in CI instead of the plain misconfiguration it was.
        if not settings.configured:
            msg = "Storage endpoint and credentials are not configured."
            raise StorageValidationError(msg)
        if settings.endpoint:
            require_https(settings.endpoint, field="S3_ENDPOINT")
        enforce_retirement(settings)
        self._encryption = encryption or ObjectEncryption()
        self._settings = settings
        self._env: StorageEnv = settings.env
        self._log = access_log or null_access_log
        factory = client_factory or boto3.client
        self._s3 = factory(
            "s3",
            verify=get_settings().tls.ca_bundle or True,
            use_ssl=True,
            endpoint_url=settings.endpoint,
            aws_access_key_id=settings.access_key,
            aws_secret_access_key=settings.secret_key,
            region_name=settings.region,
            config=BotoConfig(
                s3={"addressing_style": "path" if settings.force_path_style else "auto"},
                retries={"max_attempts": 5, "mode": "adaptive"},
            ),
        )

        if client_factory is None:
            require_boto_tls(cast(object, self._s3))

    def for_session(self, db: Session) -> Self:
        bound = copy(self)
        bound._encryption = self._encryption.for_session(db)
        return bound

    # -- contract operations ------------------------------------------------

    def write(
        self,
        location: StorageLocation,
        data: BinaryIO,
        metadata: ObjectMetadata,
        content_type: str = "application/octet-stream",
    ) -> StorageObject:
        refuse_sealed_maintenance(location, "a generic write")
        with self._encryption.publication(location) as encryption:
            bound = copy(self)
            bound._encryption = encryption
            return bound._write(location, data, metadata, content_type)

    def write_sealed(
        self,
        location: StorageLocation,
        data: BinaryIO,
        metadata: ObjectMetadata,
        authenticated_context: StorageLocation,
        content_type: str = "application/octet-stream",
    ) -> SealedPublication:
        if location.tier != "outputs" or not is_sealed_object_key(location.object_path):
            raise StorageValidationError(
                "Sealed publication requires an immutable key in the outputs tier."
            )
        self._validate_metadata(location, metadata)
        self._require_output_retention_policy(location)
        payload = _verified_payload(data, metadata.checksum_sha256)
        try:
            with self._encryption.publication(location) as encryption:
                bound = copy(self)
                bound._encryption = encryption
                return bound._create_sealed(
                    location, payload, metadata, authenticated_context, content_type
                )
        finally:
            payload.close()

    def _write(
        self,
        location: StorageLocation,
        data: BinaryIO,
        metadata: ObjectMetadata,
        content_type: str,
    ) -> StorageObject:
        self._validate_metadata(location, metadata)
        self._require_output_retention_policy(location)
        bucket = location.bucket_name(self._env)

        existing = self._stat_or_none(bucket, location.object_path)
        if existing is not None:
            existing_metadata = {
                key.lower(): value for key, value in existing.get("Metadata", {}).items()
            }
            if existing_metadata.get("checksum-sha256") == metadata.checksum_sha256:
                _object, verified = self.read(location, version_id=existing.get("VersionId"))
                verified.close()
                self._log("write.noop", location, version_id=existing.get("VersionId"))
                return self._to_storage_object(location, existing)

        try:
            encrypted, envelope_id, bank_key_id = self._encryption.encrypt(location, data, metadata)
        except (StorageAccessError, KeyUnavailableError):
            self._log("write", location, result="key-refused")
            raise
        effective_sse_key = self._settings.kms_key_id
        if envelope_id is None:
            effective_sse_key = metadata.kms_key_id or effective_sse_key
        metadata = replace(metadata, kms_key_id=bank_key_id or effective_sse_key)
        headers = metadata.to_object_metadata()
        if envelope_id is not None:
            headers.update(
                {"encryption-format": ENCRYPTION_FORMAT, "key-envelope-id": str(envelope_id)}
            )
        extra_args: dict[str, Any] = {
            "ContentType": content_type,
            "Metadata": headers,
        }
        if effective_sse_key is not None:
            extra_args["ServerSideEncryption"] = "aws:kms"
            extra_args["SSEKMSKeyId"] = effective_sse_key
        # upload_fileobj auto-switches to a multipart upload above the threshold,
        # so a large staged bundle is sent in parts each under the backend/proxy
        # request-size limit; a single put_object would 413 on a >100 MB object.
        # The managed transfer returns no response body, so the version id comes
        # from the follow-up stat (the write we just made is the latest version).
        try:
            self._call(
                "write",
                location,
                lambda: self._s3.upload_fileobj(
                    encrypted,
                    bucket,
                    location.object_path,
                    ExtraArgs=extra_args,
                    Config=_UPLOAD_TRANSFER_CONFIG,
                ),
            )
        finally:
            encrypted.close()
        stat = self._call(
            "write.stat",
            location,
            lambda: self._stat_object(bucket, location.object_path),
            log=False,
        )
        version_id = cast(str | None, stat.get("VersionId"))
        self._log("write", location, version_id=version_id)
        return self._to_storage_object(location, stat, version_id=version_id)

    def _create_sealed(  # noqa: PLR0913 - one write is its named parts
        self,
        location: StorageLocation,
        payload: BinaryIO,
        metadata: ObjectMetadata,
        authenticated_context: StorageLocation,
        content_type: str,
    ) -> SealedPublication:
        """Create the sealed key once; a same-content retry returns what is there."""
        bucket = location.bucket_name(self._env)
        try:
            encrypted, envelope_id, bank_key_id = self._encryption.encrypt(
                location, payload, metadata, authenticated_context=authenticated_context
            )
        except (StorageAccessError, KeyUnavailableError):
            self._log("write.sealed", location, result="key-refused")
            raise
        effective_sse_key = self._settings.kms_key_id
        if envelope_id is None:
            effective_sse_key = metadata.kms_key_id or effective_sse_key
        metadata = replace(metadata, kms_key_id=bank_key_id or effective_sse_key)
        headers = metadata.to_object_metadata()
        if envelope_id is not None:
            headers.update(
                {"encryption-format": ENCRYPTION_FORMAT, "key-envelope-id": str(envelope_id)}
            )
        arguments: dict[str, object] = {"ContentType": content_type, "Metadata": headers}
        if effective_sse_key is not None:
            arguments["ServerSideEncryption"] = "aws:kms"
            arguments["SSEKMSKeyId"] = effective_sse_key
        context_format: Literal["aws-esdk-object-v1", "plaintext-v1"] = (
            ENCRYPTION_FORMAT if envelope_id is not None else "plaintext-v1"
        )
        try:
            version_id = self._create_absorbing_conflicts(bucket, location, encrypted, arguments)
        except ClientError as exc:
            if _error_code(exc) not in _PRECONDITION_FAILED_CODES:
                self._log("write.sealed", location, result="error")
                raise self._translate(exc) from exc
            # The key already exists. Only the identical sealed bytes may be
            # accepted, and only after the object's own recorded context
            # authenticates them: this attempt's freshly prepared envelope is
            # NOT the one those bytes were sealed under once the bank key has
            # rotated, so the identity comes from the object, never the attempt.
            self._log("write.sealed", location, result="exists")
            return self._existing_sealed(location, metadata.checksum_sha256, authenticated_context)
        except BotoCoreError as exc:
            self._log("write.sealed", location, result="backend_error")
            raise StorageBackendError(str(exc)) from exc
        finally:
            if encrypted is not payload:
                encrypted.close()
        stat = cast(
            Mapping[str, object],
            self._call(
                "write.sealed.stat",
                location,
                lambda: self._stat_object(bucket, location.object_path),
                log=False,
            ),
        )
        if _recorded_metadata(stat).get("checksum-sha256") != metadata.checksum_sha256:
            raise StorageValidationError("Sealed publication did not record its own checksum.")
        self._log("write.sealed", location, version_id=version_id)
        published = stat.get("VersionId") or version_id
        descriptor = self._to_storage_object(
            location,
            cast(dict[str, Any], stat),
            version_id=cast(str | None, published),
        )
        return SealedPublication(descriptor, context_format, envelope_id)

    def _create_absorbing_conflicts(
        self,
        bucket: str,
        location: StorageLocation,
        body: BinaryIO,
        arguments: dict[str, object],
    ) -> str | None:
        """Create the sealed key, absorbing S3's retryable conditional conflict.

        ``If-None-Match: *`` answers ``409 ConditionalRequestConflict`` when a
        competing conditional write to the same key is still in flight. S3
        documents that as a retry, not as "the key already exists", so treating
        it as a backend failure would fail a publication that a moment later
        resolves cleanly. Retrying keeps the create atomic: the winner is still
        discovered by the same precondition (412), which is the path that
        compares content and adopts the object that exists. Every attempt
        rewinds the body and, above the multipart threshold, creates and on
        failure aborts its own upload, so a retry never resumes a stale one.
        """
        attempts = _SEALED_CREATE_ATTEMPTS
        while True:
            try:
                return self._create_only(bucket, location.object_path, body, arguments)
            except ClientError as exc:
                if _error_code(exc) not in _CONDITIONAL_CONFLICT_CODES or attempts == 1:
                    raise
                attempts -= 1
                self._log("write.sealed", location, result="conditional-conflict")
                _ = body.seek(0)

    def _create_only(
        self,
        bucket: str,
        key: str,
        body: BinaryIO,
        arguments: dict[str, object],
    ) -> str | None:
        """PUT the body only if the key does not exist (S3 ``If-None-Match: *``).

        ``put_object`` carries the precondition for a single request; a payload
        above the multipart threshold completes under the same precondition, so
        the atomic create is preserved at every size. The managed transfer
        cannot express it, which is why this path is written out.
        """
        client = cast(_S3CreateClient, cast(object, self._s3))
        size = body.seek(0, 2)
        _ = body.seek(0)
        if size < reencryption.MULTIPART_SIZE:
            response = client.put_object(
                Bucket=bucket, Key=key, Body=body, IfNoneMatch="*", **arguments
            )
            return cast(str | None, response.get("VersionId"))
        upload = client.create_multipart_upload(Bucket=bucket, Key=key, **arguments)
        upload_id = str(upload["UploadId"])
        location: dict[str, object] = {"Bucket": bucket, "Key": key, "UploadId": upload_id}
        parts: list[dict[str, object]] = []
        try:
            for number, chunk in enumerate(
                iter(lambda: body.read(reencryption.MULTIPART_SIZE), b""), start=1
            ):
                part = client.upload_part(**location, PartNumber=number, Body=chunk)
                parts.append({"ETag": part["ETag"], "PartNumber": number})
            response = client.complete_multipart_upload(
                **location, MultipartUpload={"Parts": parts}, IfNoneMatch="*"
            )
        except BaseException:
            with suppress(ClientError, BotoCoreError):
                _ = client.abort_multipart_upload(**location)
            raise
        return cast(str | None, response.get("VersionId"))

    def _existing_sealed(
        self,
        location: StorageLocation,
        expected_sha256: str,
        authenticated_context: StorageLocation,
    ) -> SealedPublication:
        """Accept an existing sealed key only for byte-identical content.

        The sealed context is rebuilt from what the OBJECT records, not from the
        attempt that lost the race. A retry can run after the bank key rotated,
        so its own envelope names a key these bytes were never sealed under;
        using it would fail the descriptor check here and mislabel the publication
        for every later read.
        """
        stat = cast(
            Mapping[str, object],
            self._call(
                "write.sealed.stat",
                location,
                lambda: self._stat_object(location.bucket_name(self._env), location.object_path),
                log=False,
            ),
        )
        if _recorded_metadata(stat).get("checksum-sha256") != expected_sha256:
            raise StorageValidationError(
                "Sealed publication refused: the immutable key already holds other content."
            )
        context = self._sealed_context(location, stat, expected_sha256, authenticated_context)
        descriptor, verified = self.read_sealed(location, context)
        with verified:
            digest = hashlib.sha256()
            while chunk := verified.read(1024 * 1024):
                digest.update(chunk)
        if digest.hexdigest() != expected_sha256:
            raise StorageValidationError(
                "Sealed publication refused: the immutable key does not hold this content."
            )
        return SealedPublication(descriptor, context.format, context.envelope_id)

    def _sealed_context(
        self,
        location: StorageLocation,
        stat: Mapping[str, object],
        expected_sha256: str,
        authenticated_context: StorageLocation,
    ) -> SealedEncryptionContext:
        """The descriptor an existing sealed object records about itself."""
        recorded = _recorded_metadata(stat)
        envelope = recorded.get("key-envelope-id")
        if recorded.get("encryption-format") == ENCRYPTION_FORMAT and envelope:
            try:
                envelope_id: UUID | None = UUID(envelope)
            except ValueError as exc:
                raise StorageValidationError(
                    "Sealed publication refused: the existing object's key envelope is invalid."
                ) from exc
            return SealedEncryptionContext(
                ENCRYPTION_FORMAT, authenticated_context, expected_sha256, envelope_id
            )
        if "encryption-format" in recorded or envelope:
            raise StorageValidationError(
                "Sealed publication refused: the existing object's encryption metadata is invalid."
            )
        return SealedEncryptionContext("plaintext-v1", authenticated_context, expected_sha256, None)

    def reencrypt(self, item: EncryptionInventoryItem) -> bool:
        result = reencryption.reencrypt(
            cast(reencryption.S3MaintenanceClient, cast(object, self._s3)),
            self._env,
            self._encryption,
            item,
            self._settings.kms_key_id,
            self._stat_object,
        )
        if result:
            self._log("reencrypt", item.location, version_id=item.version_id)
        return result

    def encryption_page(
        self, institution_slug: str, cursor: EncryptionCursor, limit: int
    ) -> EncryptionPage:
        return reencryption.inventory_page(
            cast(reencryption.S3MaintenanceClient, cast(object, self._s3)),
            self._env,
            institution_slug,
            self._stat_object,
            cursor,
            limit,
        )

    def reencrypt_isolated(
        self, item: EncryptionInventoryItem, isolation: ReencryptionIsolation
    ) -> bool:
        result = reencryption.reencrypt(
            cast(reencryption.S3MaintenanceClient, cast(object, self._s3)),
            self._env,
            self._encryption,
            item,
            self._settings.kms_key_id,
            self._stat_object,
            isolation=isolation,
        )
        if result:
            self._log("reencrypt", item.location, version_id=item.version_id)
        return result

    def retain(self, location: StorageLocation, version_id: str, until: datetime) -> None:
        self._require_output_retention_policy(location)
        if location.tier != "outputs":
            raise StorageValidationError("Filing retention requires an output object.")
        retain_version(
            cast(RetentionClient, cast(object, self._s3)),
            location.bucket_name(self._env),
            location.object_path,
            version_id,
            until,
        )
        self._log("retain", location, version_id=version_id)

    def read(
        self,
        location: StorageLocation,
        version_id: str | None = None,
    ) -> tuple[StorageObject, BinaryIO]:
        return self._read(location, version_id)

    def read_sealed(
        self, location: StorageLocation, context: SealedEncryptionContext
    ) -> tuple[StorageObject, BinaryIO]:
        return self._read(location, None, sealed_context=context)

    def _read(
        self,
        location: StorageLocation,
        version_id: str | None,
        *,
        sealed_context: SealedEncryptionContext | None = None,
    ) -> tuple[StorageObject, BinaryIO]:
        bucket = location.bucket_name(self._env)
        kwargs: dict[str, Any] = {"Bucket": bucket, "Key": location.object_path}
        if version_id is not None:
            kwargs["VersionId"] = version_id
        response = cast(
            dict[str, object],
            self._call("read", location, lambda: self._s3.get_object(**kwargs)),
        )
        body = cast(BinaryIO, response["Body"])
        try:
            descriptor = self._to_storage_object(location, response, version_id=version_id)
            headers = {
                key.lower(): value
                for key, value in _OBJECT_HEADERS.validate_python(
                    response.get("Metadata", {})
                ).items()
            }
            plaintext = self._encryption.decrypt(
                location, body, descriptor.metadata, headers, sealed_context=sealed_context
            )
        except KeyUnavailableError:
            body.close()
            self._log("read", location, result="key-refused")
            raise
        except (StorageError, ValidationError, ValueError, KeyError) as exc:
            body.close()
            self._log("read", location, result="verification-refused")
            raise StorageAccessError(
                "The bank key or encrypted object could not be verified."
            ) from exc
        self._log("read", location, version_id=descriptor.version_id)
        return descriptor, plaintext

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
        refuse_sealed_maintenance(location, "delete")
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
        audit_operation = f"presigned_url.{operation}"
        if operation == "write":
            self._require_output_retention_policy(location)
        try:
            if operation == "read":
                self._encryption.require_read_access()
            bank_key_required = self._encryption.bank_key_required(location)
        except KeyUnavailableError:
            self._log(audit_operation, location, result="key-refused")
            raise
        except (RuntimeError, SQLAlchemyError) as exc:
            self._log(audit_operation, location, result="configuration-refused")
            raise StorageAccessError("The bank key configuration could not be verified.") from exc
        if not bank_key_required:
            method = "get_object" if operation == "read" else "put_object"
            signer = cast(_S3ControlClient, cast(object, self._s3))
            url = cast(
                str,
                self._call(
                    audit_operation,
                    location,
                    lambda: signer.generate_presigned_url(
                        method,
                        Params={
                            "Bucket": location.bucket_name(self._env),
                            "Key": location.object_path,
                        },
                        ExpiresIn=expires_in_seconds,
                    ),
                ),
            )
            self._log(audit_operation, location)
            return url
        if operation != "read":
            self._log(audit_operation, location, result="key-refused")
            raise StorageAccessError(
                "Encrypted bank uploads must pass through application storage."
            )
        stat = cast(
            dict[str, object],
            self._call(
                audit_operation,
                location,
                lambda: self._stat_object(location.bucket_name(self._env), location.object_path),
            ),
        )
        version = stat.get("VersionId")
        version_id = str(version) if version is not None else None
        try:
            url = issue(location, version_id, expires_in_seconds)
        except (StorageError, RuntimeError, ValueError):
            self._log(audit_operation, location, result="signing-refused")
            raise
        self._log(audit_operation, location, version_id=version_id)
        return url

    def health_check(self) -> StorageHealth:
        try:
            client = cast(_S3ControlClient, cast(object, self._s3))
            _ = client.list_buckets()
        except (ClientError, BotoCoreError) as exc:
            return StorageHealth(healthy=False, backend=self._settings.backend, detail=str(exc))
        return StorageHealth(healthy=True, backend=self._settings.backend)

    def ensure_institution(self, institution_slug: str) -> None:
        provision_institution(
            cast(ProvisioningClient, cast(object, self._s3)), self._settings, institution_slug
        )

    def flush_access_log(self) -> str | None:
        if not isinstance(self._log, HashChainedAccessLog):
            return None
        segment = self._log.drain_segment()
        if segment is None:
            return None
        jsonl, first, last = segment
        bucket = f"aequoros-{self._env}-audit-logs"
        ensure_audit_bucket(
            cast(ProvisioningClient, cast(object, self._s3)), self._settings, bucket
        )
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

    def _require_output_retention_policy(self, location: StorageLocation) -> None:
        if self._settings.object_lock_enabled and location.tier == "outputs":
            require_object_lock(
                cast(RetentionClient, cast(object, self._s3)), location.bucket_name(self._env)
            )

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
        once the WAF rule on the managed S3 endpoint is fixed.
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
        except (BotoCoreError, S3UploadFailedError) as exc:
            # BotoCoreError covers transport/config failures; S3UploadFailedError
            # wraps a failed managed (multipart) upload — both are backend errors.
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
