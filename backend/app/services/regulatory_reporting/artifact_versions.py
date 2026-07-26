"""Which archived bytes are THE document (docs/attestation_esignature.md §3.2, §4.2).

Two tables describe the same return. ``regulatory_package_artifacts`` is upserted
per kind and therefore only ever names the CANONICAL UNSIGNED export.
``regulatory_artifact_versions`` is appended — once per render by the export
engine, once more per signature by ``attestation.artifact_signing``, each signed
revision at its own object path and pinned by ``AttestationSignature.artifact_version_id``.

Nothing outside the signing ceremony read the second table, so the artifacts card
and every submission channel resolved the document through the first one: a bank
whose officers had certified a return still downloaded, and still filed, the
document nobody had signed. This module is the resolver that closes that —
:func:`latest_signed_version` answers "what did the officers actually sign",
:func:`list_versions` exposes the whole chain, and :func:`read_version_bytes`
serves it.

**The checksum is re-verified on every read.** Serving bytes that differ from what
a signature pinned is worse than failing the download: the artifact-binding check
(§3.5 check 5) would convict the file later, in front of an examiner, instead of
here. Same reasoning as ``artifact_signing._read``, which refuses to SIGN bytes
that no longer match.

**The unsigned base export is retained and stays retrievable**, clearly labelled
as the pre-signature engine output. It is provenance — what the engine produced
before anyone touched it — never the default and never what is filed. Nothing
needs it at read time: a PAdES signed PDF is self-contained and carries the base
bytes as a literal prefix.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import AttestationSignature, RegulatoryArtifactVersion, RegulatoryPackage
from app.schemas.regulatory_reporting import (
    ArtifactVersionSignatureRead,
    RegulatoryArtifactVersionListRead,
    RegulatoryArtifactVersionRead,
)
from app.services.audit import record_event
from app.services.regulatory_reporting.common import (
    get_bank_or_404,
    get_package_or_404,
)
from app.storage.client import StorageClient, StorageError, StorageLocation


@dataclass(frozen=True)
class SignedRevision:
    """One archived revision and the signature that pinned it."""

    version: RegulatoryArtifactVersion
    signature: AttestationSignature


def package_versions(
    db: Session, ctx: TenantContext, package: RegulatoryPackage
) -> list[RegulatoryArtifactVersion]:
    """Every archived version of this package's artifacts, oldest first."""
    return list(
        db.scalars(
            select(RegulatoryArtifactVersion)
            .where(
                RegulatoryArtifactVersion.organization_id == ctx.organization_id,
                RegulatoryArtifactVersion.package_id == package.id,
            )
            .order_by(
                RegulatoryArtifactVersion.created_at,
                RegulatoryArtifactVersion.id,
            )
        )
    )


def signed_revisions(
    db: Session, ctx: TenantContext, package: RegulatoryPackage
) -> list[SignedRevision]:
    """The signed revisions of the package's CURRENT attestation cycle, in signing order.

    Scoped to the cycle for the same reason the re-export guard is
    (``exports._refuse_if_signed``): a void increments the cycle rather than
    deleting signatures, so a withdrawn attestation's document must stop being
    the one this bank downloads and files. Its bytes are untouched and still
    listed — a withdrawn attestation has to stay legible (L15) — but it is no
    longer THE document.
    """
    pinned = [
        (signature, signature.artifact_version_id)
        for signature in db.scalars(
            select(AttestationSignature)
            .where(
                AttestationSignature.organization_id == ctx.organization_id,
                AttestationSignature.package_id == package.id,
                AttestationSignature.attestation_cycle == package.attestation_cycle,
                AttestationSignature.artifact_version_id.is_not(None),
            )
            .order_by(AttestationSignature.created_at, AttestationSignature.id)
        )
        if signature.artifact_version_id is not None
    ]
    if not pinned:
        return []
    versions = {
        version.id: version
        for version in db.scalars(
            select(RegulatoryArtifactVersion).where(
                RegulatoryArtifactVersion.organization_id == ctx.organization_id,
                RegulatoryArtifactVersion.package_id == package.id,
                RegulatoryArtifactVersion.id.in_([version_id for _, version_id in pinned]),
            )
        )
    }
    return [
        SignedRevision(version=versions[version_id], signature=signature)
        for signature, version_id in pinned
        if version_id in versions
    ]


def latest_signed_version(
    db: Session, ctx: TenantContext, package: RegulatoryPackage
) -> SignedRevision | None:
    """The document as the LAST officer left it — what download and filing resolve to.

    Resolved from the signature chain rather than from the newest version row:
    the pin is the only statement of which exact bytes an officer covered, and a
    later unsigned re-export must never become the thing that gets filed. Each
    revision contains its predecessor as a literal prefix, so the last one
    carries every signature made in this cycle.
    """
    revisions = signed_revisions(db, ctx, package)
    return revisions[-1] if revisions else None


def list_versions(
    db: Session, ctx: TenantContext, bank_id: str, package_id: UUID
) -> RegulatoryArtifactVersionListRead:
    """Every archived version for a package, each naming the signature that pinned it."""
    get_bank_or_404(db, ctx, bank_id)
    package = get_package_or_404(db, ctx, bank_id, package_id)
    versions = package_versions(db, ctx, package)
    revisions = {
        revision.version.id: revision for revision in signed_revisions(db, ctx, package)
    }
    filed = latest_signed_version(db, ctx, package)
    latest_by_kind = {version.kind: version.id for version in versions}
    return RegulatoryArtifactVersionListRead(
        package_id=package.id,
        versions=[
            RegulatoryArtifactVersionRead(
                id=version.id,
                package_id=version.package_id,
                kind=version.kind,  # type: ignore[arg-type]
                object_path=version.object_path,
                storage_version_id=version.storage_version_id,
                checksum_sha256=version.checksum_sha256,
                size_bytes=version.size_bytes,
                created_at=version.created_at,
                signed_by=_signature_read(revisions[version.id].signature)
                if version.id in revisions
                else None,
                is_latest=latest_by_kind.get(version.kind) == version.id,
                is_filed=filed is not None and filed.version.id == version.id,
            )
            for version in versions
        ],
    )


def _signature_read(signature: AttestationSignature) -> ArtifactVersionSignatureRead:
    return ArtifactVersionSignatureRead(
        signature_id=signature.id,
        signing_role=signature.signing_role,  # type: ignore[arg-type]
        signer_id=signature.signer_id,
        signer_display_name=signature.signer_display_name,
        officer_title=signature.officer_title,
        # The trusted RFC 3161 time is authoritative where the deployment has a
        # TSA; declared_at (the server clock) is the documented fallback.
        signed_at=signature.tsa_time or signature.declared_at,
        attestation_cycle=signature.attestation_cycle,
    )


def get_version_or_404(
    db: Session, ctx: TenantContext, bank_id: str, version_id: UUID
) -> RegulatoryArtifactVersion:
    """Tenant-scoped version lookup, constrained to the bank via its package."""
    get_bank_or_404(db, ctx, bank_id)
    version = db.scalar(
        select(RegulatoryArtifactVersion)
        .join(
            RegulatoryPackage,
            (RegulatoryPackage.id == RegulatoryArtifactVersion.package_id)
            & (RegulatoryPackage.organization_id == RegulatoryArtifactVersion.organization_id),
        )
        .where(
            RegulatoryArtifactVersion.id == version_id,
            RegulatoryArtifactVersion.organization_id == ctx.organization_id,
            RegulatoryPackage.bank_id == bank_id,
        )
    )
    if version is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Regulatory artifact version not found.",
        )
    return version


def read_version_bytes(
    db: Session,
    ctx: TenantContext,
    bank_id: str,
    version_id: UUID,
    storage: StorageClient,
) -> tuple[RegulatoryArtifactVersion, bytes]:
    """Resolve, fetch, prove, and audit one archived revision.

    Whole-payload rather than a passthrough stream: a checksum cannot be checked
    on bytes already sent, and half a signed return followed by a 500 is not a
    download an operator can distinguish from a good one.
    """
    bank = get_bank_or_404(db, ctx, bank_id)
    version = get_version_or_404(db, ctx, bank_id, version_id)
    # Lazy import, matching prepare_artifact_download: app.services.ingestion
    # drags the whole source-adapter registry into every importer.
    from app.services.ingestion import bank_slug  # noqa: PLC0415

    location = StorageLocation(
        institution_slug=bank_slug(db, bank), tier="outputs", object_path=version.object_path
    )
    try:
        _descriptor, stream = storage.read(location, version_id=version.storage_version_id)
        payload = stream.read()
    except StorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"The stored object for this artifact version ({version.object_path}) "
                "could not be read from the outputs tier."
            ),
        ) from exc
    digest = hashlib.sha256(payload).hexdigest()
    if digest != version.checksum_sha256:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "artifact_version_checksum_mismatch",
                "message": (
                    f"The archived document at {version.object_path} no longer matches the "
                    f"checksum recorded when it was written (recorded "
                    f"{version.checksum_sha256[:12]}…, read {digest[:12]}…). It is not "
                    "served: these are the exact bytes a signature covers, so a mismatch "
                    "is object-store corruption, not a stale render. Escalate it."
                ),
            },
        )
    record_event(
        db,
        ctx,
        event_type="regulatory_artifact_version.downloaded",
        entity_type="regulatory_artifact_version",
        entity_id=version.id,
        details={
            "package_id": str(version.package_id),
            "kind": version.kind,
            "object_path": version.object_path,
            "storage_version_id": version.storage_version_id,
            "checksum_sha256": version.checksum_sha256,
        },
    )
    db.commit()
    return version, payload


__all__ = [
    "SignedRevision",
    "get_version_or_404",
    "latest_signed_version",
    "list_versions",
    "package_versions",
    "read_version_bytes",
    "signed_revisions",
]
