"""Confirm compliance retention for the exact archived filing versions."""

from __future__ import annotations

from collections.abc import Generator, Sequence
from contextlib import contextmanager
from datetime import datetime
from typing import TypedDict

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.attestation.public import RegulatoryArtifactVersion
from app.core.tenancy import TenantContext
from app.reporting.models.packages import RegulatoryPackage
from app.reporting.service.artifact_versions import archived_version
from app.reporting.service.channels import FiledArtifact
from app.reporting.service.common import get_bank_or_404
from app.storage.client import StorageError, StorageLocation, StorageValidationError
from app.storage.config import get_storage_settings
from app.storage.factory import get_storage_client
from app.storage.public import DocumentBinding, DocumentFamily, document_retention_reference
from app.storage.retention import retention_until


def filing_version(
    db: Session,
    package: RegulatoryPackage,
    artifact: FiledArtifact,
    *,
    before: datetime | None = None,
) -> RegulatoryArtifactVersion:
    if isinstance(artifact, RegulatoryArtifactVersion):
        return artifact
    version = archived_version(
        db,
        TenantContext(package.organization_id),
        package_id=package.id,
        kind=artifact.kind,
        object_path=artifact.object_path,
        checksum_sha256=artifact.checksum_sha256,
        before=before,
    )
    if version is None:
        raise StorageValidationError("The filed artifact has no archived object version.")
    return version


class RetainedArtifact(TypedDict):
    kind: str
    object_path: str
    checksum_sha256: str
    size_bytes: int
    signed: bool
    storage_version_id: str


@contextmanager
def filing_retention_errors() -> Generator[None]:
    try:
        yield
    except StorageError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error_code": "filing_retention_unconfirmed",
                "message": "The filed object versions could not be retained. Submission refused.",
            },
        ) from exc


def retain_filing(
    db: Session, ctx: TenantContext, package: RegulatoryPackage, artifacts: Sequence[FiledArtifact]
) -> list[RetainedArtifact] | None:
    settings = get_storage_settings()
    if not settings.object_lock_enabled:
        return None
    with filing_retention_errors():
        client = get_storage_client()
        bank = get_bank_or_404(db, ctx, package.bank_id)
        slug = bank.storage_slug
        if slug is None:
            raise StorageValidationError("The bank has no existing storage location.")
        until = retention_until(settings)
        retained: list[RetainedArtifact] = []
        for artifact in artifacts:
            version = filing_version(db, package, artifact)
            location, version_id = document_retention_reference(
                db,
                package.organization_id,
                package.bank_id,
                DocumentBinding(DocumentFamily.ARTIFACT, version.id),
                StorageLocation(slug, "outputs", version.object_path),
                version.storage_version_id,
            )
            if not version_id or version_id == "null":
                raise StorageValidationError("Filing retention requires a concrete object version.")
            client.retain(location, version_id, until)
            retained.append(
                {
                    "kind": artifact.kind,
                    "object_path": artifact.object_path,
                    "checksum_sha256": artifact.checksum_sha256,
                    "size_bytes": artifact.size_bytes,
                    "signed": isinstance(artifact, RegulatoryArtifactVersion),
                    "storage_version_id": version_id,
                }
            )
        return retained
