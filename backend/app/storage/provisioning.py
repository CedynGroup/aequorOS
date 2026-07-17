"""Institution storage provisioning for the MinIO/S3-protocol backend (§3.3).

One call provisions everything a new institution needs: four tier buckets,
versioning on the retained tiers, and the aggressive lifecycle rule on
``temp``. Idempotent by design — re-running against an already-provisioned
institution is a no-op, so onboarding automation can safely retry.

Manual bucket creation in a console is prohibited outside debugging; this
module is the only sanctioned path (Terraform takes over for GCS/S3 in
Phase 2).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from botocore.exceptions import ClientError

from app.storage.client import RETAINED_TIERS, TIERS, StorageLocation, Tier
from app.storage.config import StorageEngineSettings

logger = logging.getLogger(__name__)

TEMP_EXPIRY_DAYS = 30


@dataclass(frozen=True)
class ProvisioningResult:
    institution_slug: str
    created_buckets: list[str] = field(default_factory=list)
    existing_buckets: list[str] = field(default_factory=list)

    @property
    def bucket_names(self) -> list[str]:
        return sorted(self.created_buckets + self.existing_buckets)


def provision_institution(
    s3_client,  # boto3 S3 client
    settings: StorageEngineSettings,
    institution_slug: str,
) -> ProvisioningResult:
    created: list[str] = []
    existing: list[str] = []

    for tier in TIERS:
        bucket = StorageLocation(
            institution_slug=institution_slug, tier=tier, object_path=""
        ).bucket_name(settings.env)
        if _bucket_exists(s3_client, bucket):
            existing.append(bucket)
        else:
            # MinIO requires an explicit LocationConstraint even for regions
            # where AWS S3 would reject one (probed against the managed MinIO deployment).
            s3_client.create_bucket(
                Bucket=bucket,
                CreateBucketConfiguration={"LocationConstraint": settings.region},
            )
            created.append(bucket)
            logger.info("provisioned bucket %s", bucket)

        if tier in RETAINED_TIERS:
            s3_client.put_bucket_versioning(
                Bucket=bucket, VersioningConfiguration={"Status": "Enabled"}
            )
        if tier == "temp":
            _ensure_temp_lifecycle(s3_client, bucket)
        if settings.kms_key_id is not None:
            _ensure_default_encryption(s3_client, bucket, settings.kms_key_id)

    return ProvisioningResult(
        institution_slug=institution_slug,
        created_buckets=created,
        existing_buckets=existing,
    )


def deprovision_institution(
    s3_client,
    settings: StorageEngineSettings,
    institution_slug: str,
    *,
    tiers: tuple[Tier, ...] = TIERS,
) -> list[str]:
    """Delete an institution's buckets and all contents. Test/dev use only.

    Production offboarding never deletes (storage.md §6.4): buckets go
    read-only and data outlives the customer relationship for the balance of
    regulatory retention.
    """
    removed: list[str] = []
    for tier in tiers:
        bucket = StorageLocation(
            institution_slug=institution_slug, tier=tier, object_path=""
        ).bucket_name(settings.env)
        if not _bucket_exists(s3_client, bucket):
            continue
        paginator = s3_client.get_paginator("list_object_versions")
        for page in paginator.paginate(Bucket=bucket):
            doomed = [
                {"Key": entry["Key"], "VersionId": entry["VersionId"]}
                for group in ("Versions", "DeleteMarkers")
                for entry in page.get(group, [])
            ]
            if doomed:
                s3_client.delete_objects(Bucket=bucket, Delete={"Objects": doomed})
        s3_client.delete_bucket(Bucket=bucket)
        removed.append(bucket)
    return removed


def ensure_audit_bucket(s3_client, settings: StorageEngineSettings, bucket: str) -> None:
    """The platform-wide audit bucket: versioned, encrypted, never lifecycled.

    Audit segments are retained for 7+ years (storage.md §9.2); no lifecycle
    rule is set so nothing ever ages out implicitly.
    """
    if not _bucket_exists(s3_client, bucket):
        s3_client.create_bucket(
            Bucket=bucket,
            CreateBucketConfiguration={"LocationConstraint": settings.region},
        )
        logger.info("provisioned audit bucket %s", bucket)
    s3_client.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={"Status": "Enabled"})
    if settings.kms_key_id is not None:
        _ensure_default_encryption(s3_client, bucket, settings.kms_key_id)


def _ensure_default_encryption(s3_client, bucket: str, kms_key_id: str) -> None:
    # MinIO requires the key ID inside the rule (a bare aws:kms rule is
    # rejected as MalformedXML — probed 2026-07-15).
    s3_client.put_bucket_encryption(
        Bucket=bucket,
        ServerSideEncryptionConfiguration={
            "Rules": [
                {
                    "ApplyServerSideEncryptionByDefault": {
                        "SSEAlgorithm": "aws:kms",
                        "KMSMasterKeyID": kms_key_id,
                    }
                }
            ]
        },
    )


def _bucket_exists(s3_client, bucket: str) -> bool:
    try:
        s3_client.head_bucket(Bucket=bucket)
        return True
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in {"404", "NoSuchBucket", "NotFound"}:
            return False
        raise


def _ensure_temp_lifecycle(s3_client, bucket: str) -> None:
    s3_client.put_bucket_lifecycle_configuration(
        Bucket=bucket,
        LifecycleConfiguration={
            "Rules": [
                {
                    "ID": f"temp-expiry-{TEMP_EXPIRY_DAYS}d",
                    "Status": "Enabled",
                    "Filter": {},
                    "Expiration": {"Days": TEMP_EXPIRY_DAYS},
                }
            ]
        },
    )
