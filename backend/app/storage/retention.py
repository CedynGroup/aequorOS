"""Compliance retention for filed output versions and disaster-recovery artifacts."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Protocol

# pyright: reportMissingTypeStubs=false
from boto3.exceptions import S3UploadFailedError
from botocore.exceptions import BotoCoreError, ClientError
from pydantic import TypeAdapter, ValidationError

from app.storage.client import StorageBackendError, StorageLocation, StorageValidationError
from app.storage.config import StorageEngineSettings


class RetentionClient(Protocol):
    def get_object_lock_configuration(self, *, Bucket: str) -> dict[str, object]: ...

    def get_object_retention(
        self, *, Bucket: str, Key: str, VersionId: str
    ) -> dict[str, object]: ...

    def put_object_retention(
        self, *, Bucket: str, Key: str, VersionId: str, Retention: dict[str, object]
    ) -> object: ...


def retention_until(settings: StorageEngineSettings) -> datetime:
    return datetime.now(UTC) + timedelta(days=settings.object_lock_retention_days)


def filing_retention(settings: StorageEngineSettings, location: StorageLocation) -> datetime | None:
    if settings.object_lock_enabled and location.tier == "outputs":
        return retention_until(settings)
    return None


@contextmanager
def retention_errors() -> Generator[None]:
    """Keep vendor and transport details inside the storage boundary."""
    try:
        yield
    except (ClientError, BotoCoreError, S3UploadFailedError, ValidationError) as exc:
        raise StorageBackendError("Compliance retention could not be verified or applied.") from exc


def require_object_lock(client: RetentionClient, bucket: str) -> None:
    with retention_errors():
        config = TypeAdapter(dict[str, object]).validate_python(
            client.get_object_lock_configuration(Bucket=bucket).get("ObjectLockConfiguration", {})
        )
        if config.get("ObjectLockEnabled") != "Enabled":
            raise StorageValidationError(
                "Compliance retention requires an Object Lock enabled bucket."
            )


def retain_version(
    client: RetentionClient,
    bucket: str,
    key: str,
    version: str | None,
    until: datetime,
) -> None:
    """Extend existing retention if needed; never shorten a compliance lock."""
    if not version or version == "null":
        raise StorageValidationError("Compliance retention requires a concrete object version.")
    with retention_errors():
        try:
            response = client.get_object_retention(Bucket=bucket, Key=key, VersionId=version)
        except ClientError as exc:
            error_response = TypeAdapter(dict[str, object]).validate_python(exc.response)
            error = TypeAdapter(dict[str, object]).validate_python(error_response.get("Error", {}))
            if error.get("Code") != "NoSuchObjectLockConfiguration":
                raise
            # Existing versions can predate the Object Lock rollout. The PUT
            # below still refuses a bucket that has not enabled Object Lock.
            response = {}
        current = TypeAdapter(dict[str, object]).validate_python(response.get("Retention", {}))
        date = current.get("RetainUntilDate")
        if current.get("Mode") == "COMPLIANCE" and isinstance(date, datetime) and date >= until:
            return
        _ = client.put_object_retention(
            Bucket=bucket,
            Key=key,
            VersionId=version,
            Retention={"Mode": "COMPLIANCE", "RetainUntilDate": until},
        )
