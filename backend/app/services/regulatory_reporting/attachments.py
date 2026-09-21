"""Documents filed WITH a return, and the submission gate that counts them.

``return_signing_policies.required_attachments`` has existed since the
attestation spine landed, and until now **nothing could satisfy it**: there was
no table to hold an attachment, no endpoint to upload one and no check that
counted them, so a policy naming ``board_resolution`` silently passed. This
module closes that (audit C-6), and it closes it for EVERY family — a Board
resolution requirement written against an ICAAP return and one written against
a quarterly liquidity return are the same requirement and are enforced by the
same code.

Two sources, one manifest:

* **``package_upload``** — a document uploaded against this package. The media
  type is sniffed from the bytes (``services/document_uploads.py``), never taken
  from the client, and the upload row is UNALTERABLE: withdrawing a document is
  a separate event, so "which document did we file" survives anyone's tidying.
* **``icaap_cycle``** — a document the ICAAP cycle already held, carried onto
  the package by reference at freeze. The bytes are NOT copied: the cycle's own
  upload table is unalterable too, so the object at that path cannot change and
  the sha256 is the binding.

The kill switch is deliberately out of scope here. ``ATTESTATION_ESIGN_REQUIRED=0``
suspends SIGNATURES; it has never had anything to say about whether the Board's
resolution accompanies the filing, and letting it exempt documents would turn a
signing-infrastructure switch into a regulatory one.
"""

from __future__ import annotations

import hashlib
from io import BytesIO
from typing import TYPE_CHECKING, Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.db.base import utc_now
from app.models import (
    Bank,
    RegulatoryPackage,
    RegulatoryPackageAttachment,
    RegulatoryPackageAttachmentWithdrawal,
)
from app.models.regulatory_reporting import PACKAGE_ATTACHMENT_GATES
from app.schemas.regulatory_reporting import (
    PackageAttachmentListRead,
    PackageAttachmentRead,
    PackageAttachmentRequirementRead,
)
from app.services.audit import record_event
from app.services.document_uploads import ALLOWED_MEDIA, sniff_media_type
from app.services.regulatory_reporting import family_hooks
from app.storage import ObjectMetadata, StorageClient, StorageLocation

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.services.attestation.policy import SigningPolicy

_STORAGE_TIER = "outputs"
WRITTEN_BY = "package_attachment"

#: A document may be attached while the return is still being prepared, routed
#: or approved. Once it is SUBMITTED the manifest is the record of what went to
#: the regulator and stops moving.
ATTACHABLE_STATUSES: frozenset[str] = frozenset(
    {"generated", "validated", "pending_approval", "approved"}
)

#: The kind every family accepts for a document that is not a named requirement.
SUPPORTING_DOCUMENT = "supporting_document"

#: Kinds whose own facts must accompany the upload, and which facts. A Board
#: resolution with no date and no reference cannot be cited in a filing.
REQUIRED_ATTRIBUTES: dict[str, tuple[str, ...]] = {
    "board_resolution": ("resolution_date", "resolution_reference"),
}


def _conflict(error_code: str, message: str, **extra: Any) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"error_code": error_code, "message": message, **extra},
    )


# --- reads ------------------------------------------------------------------


def _withdrawals(
    db: Session, ctx: TenantContext, package: RegulatoryPackage
) -> dict[UUID, RegulatoryPackageAttachmentWithdrawal]:
    return {
        row.attachment_id: row
        for row in db.scalars(
            select(RegulatoryPackageAttachmentWithdrawal).where(
                RegulatoryPackageAttachmentWithdrawal.organization_id == ctx.organization_id,
                RegulatoryPackageAttachmentWithdrawal.package_id == package.id,
            )
        )
    }


def _rows(
    db: Session, ctx: TenantContext, package: RegulatoryPackage
) -> list[RegulatoryPackageAttachment]:
    return list(
        db.scalars(
            select(RegulatoryPackageAttachment)
            .where(
                RegulatoryPackageAttachment.organization_id == ctx.organization_id,
                RegulatoryPackageAttachment.package_id == package.id,
            )
            .order_by(RegulatoryPackageAttachment.created_at.asc())
        )
    )


def active_attachments(
    db: Session, ctx: TenantContext, package: RegulatoryPackage
) -> list[RegulatoryPackageAttachment]:
    """Every attachment that has NOT been withdrawn, oldest first."""
    withdrawn = set(_withdrawals(db, ctx, package))
    return [row for row in _rows(db, ctx, package) if row.id not in withdrawn]


def active_counts(
    db: Session, ctx: TenantContext, package: RegulatoryPackage
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in active_attachments(db, ctx, package):
        counts[row.kind] = counts.get(row.kind, 0) + 1
    return counts


def manifest(
    db: Session, ctx: TenantContext, package: RegulatoryPackage
) -> list[dict[str, Any]]:
    """What accompanied this filing, for the submission event's own record.

    Channels transmit ARTIFACTS; an attachment manifest records which documents
    the filing was made with and their hashes, so "what did we actually file"
    is answerable from the database years later.
    """
    return [
        {
            "attachment_id": str(row.id),
            "kind": row.kind,
            "title": row.title,
            "sha256": row.sha256,
            "byte_size": row.byte_size,
            "source": row.source,
            "gate": row.gate,
        }
        for row in active_attachments(db, ctx, package)
    ]


def _read(
    row: RegulatoryPackageAttachment,
    withdrawal: RegulatoryPackageAttachmentWithdrawal | None,
) -> PackageAttachmentRead:
    return PackageAttachmentRead(
        id=row.id,
        package_id=row.package_id,
        package_version=row.package_version,
        kind=row.kind,
        title=row.title,
        original_filename=row.original_filename,
        media_type=row.media_type,
        byte_size=row.byte_size,
        sha256=row.sha256,
        source=row.source,  # type: ignore[arg-type]
        gate=row.gate,  # type: ignore[arg-type]
        attributes=dict(row.attributes or {}),
        attached_by=row.attached_by,
        created_at=row.created_at,
        withdrawn=withdrawal is not None,
        withdrawn_at=None if withdrawal is None else withdrawal.created_at,
        withdrawal_reason=None if withdrawal is None else withdrawal.reason,
    )


# --- what is required -------------------------------------------------------


def required_attachments(
    db: Session, ctx: TenantContext, package: RegulatoryPackage, policy: SigningPolicy
) -> dict[str, tuple[int, str]]:
    """``{kind: (min_count, origin)}`` — the union of both sources.

    ``policy.required_attachments`` is what the INSTITUTION requires of itself
    (an audited settings change); the family hook is what the FRAMEWORK requires
    of the filing. The second is not relaxable by the first: dropping a Board
    signature slot changes who signs, never whether the Board's resolution
    accompanies the return (audit M11).
    """
    required: dict[str, tuple[int, str]] = {
        kind: (1, "signing_policy") for kind in policy.required_attachments
    }
    for kind, count in family_hooks.required_attachments(db, ctx, package).items():
        existing = required.get(kind)
        # The stricter count wins, and a family requirement keeps its origin so
        # the UI can say why it cannot be relaxed.
        best = max(count, existing[0]) if existing is not None else count
        required[kind] = (best, "family")
    return required


def requirement_rows(
    db: Session, ctx: TenantContext, package: RegulatoryPackage, policy: SigningPolicy
) -> list[PackageAttachmentRequirementRead]:
    counts = active_counts(db, ctx, package)
    gates = {row.kind: row.gate for row in active_attachments(db, ctx, package)}
    rows = []
    for kind, (needed, origin) in sorted(required_attachments(db, ctx, package, policy).items()):
        present = counts.get(kind, 0)
        rows.append(
            PackageAttachmentRequirementRead(
                kind=kind,
                title=_humanised(kind),
                gate=gates.get(kind, "submission"),  # type: ignore[arg-type]
                origin=origin,  # type: ignore[arg-type]
                required_count=needed,
                active_count=present,
                satisfied=present >= needed,
            )
        )
    return rows


def _humanised(kind: str) -> str:
    return kind.replace("_", " ").strip().capitalize()


def ensure_required_attachments(
    db: Session, ctx: TenantContext, package: RegulatoryPackage, policy: SigningPolicy
) -> None:
    """Refuse a submission that is missing a document it must be filed with.

    Counts ACTIVE (non-withdrawn) rows for THIS package id. Withdrawing the only
    Board resolution therefore blocks the submission again, which is the point:
    a withdrawal says the document should not have been filed.
    """
    counts = active_counts(db, ctx, package)
    missing = [
        {"kind": kind, "required": needed, "present": counts.get(kind, 0), "origin": origin}
        for kind, (needed, origin) in sorted(
            required_attachments(db, ctx, package, policy).items()
        )
        if counts.get(kind, 0) < needed
    ]
    if not missing:
        return
    names = ", ".join(_humanised(str(entry["kind"])) for entry in missing)
    raise _conflict(
        "attachments_missing",
        f"This return is filed with supporting documents that are not attached yet: {names}.",
        missing=missing,
    )


def list_attachments(
    db: Session, ctx: TenantContext, package: RegulatoryPackage, policy: SigningPolicy
) -> PackageAttachmentListRead:
    withdrawals = _withdrawals(db, ctx, package)
    return PackageAttachmentListRead(
        package_id=package.id,
        attachments=[_read(row, withdrawals.get(row.id)) for row in _rows(db, ctx, package)],
        requirements=requirement_rows(db, ctx, package, policy),
    )


# --- writes -----------------------------------------------------------------


def _allowed_kinds(
    db: Session, ctx: TenantContext, package: RegulatoryPackage, policy: SigningPolicy
) -> dict[str, str]:
    """``{kind: gate}`` a caller may upload against this package.

    Anything the filing genuinely requires, plus the generic supporting
    document. A kind nobody asked for is refused rather than stored under a
    name no requirement will ever count.
    """
    allowed = {
        kind: "submission" for kind in required_attachments(db, ctx, package, policy)
    }
    allowed.setdefault(SUPPORTING_DOCUMENT, "optional")
    return allowed


def upload_package_attachment(  # noqa: PLR0913 - an upload is its named parts
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    package: RegulatoryPackage,
    storage: StorageClient,
    *,
    kind: str,
    title: str,
    filename: str,
    content: bytes,
    attributes: dict[str, Any] | None = None,
    max_bytes: int,
) -> PackageAttachmentRead:
    """Attach one document to a package, proving what it is from its bytes."""
    from app.services.attestation.workflow import package_policy  # noqa: PLC0415

    actor_user_id = ctx.actor_user_id
    if actor_user_id is None:  # pragma: no cover - refused upstream
        raise _conflict("actor_required", "This action requires a signed-in user.")
    if package.status not in ATTACHABLE_STATUSES:
        raise _conflict(
            "package_not_attachable",
            "Documents are attached while a return is being prepared and approved; "
            f"this return is '{package.status}'.",
            status=package.status,
        )
    policy = package_policy(db, ctx, package)
    allowed = _allowed_kinds(db, ctx, package, policy)
    if kind not in allowed:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "error_code": "attachment_kind_unknown",
                "message": f"'{kind}' is not a document this return is filed with.",
                "allowed": sorted(allowed),
            },
        )
    if not content:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"error_code": "attachment_empty", "message": "The file is empty."},
        )
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={
                "error_code": "attachment_too_large",
                "message": f"Attachments are limited to {max_bytes} bytes.",
                "byte_size": len(content),
            },
        )
    media_type = sniff_media_type(content)
    if media_type is None:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail={
                "error_code": "unsupported_media_type",
                "message": "A filed document must be a PDF, Word or Excel file.",
            },
        )
    missing_attributes = [
        name
        for name in REQUIRED_ATTRIBUTES.get(kind, ())
        if not str((attributes or {}).get(name, "")).strip()
    ]
    if missing_attributes:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "error_code": "attachment_attributes_required",
                "message": (
                    f"{_humanised(kind)} must record: "
                    f"{', '.join(_humanised(name) for name in missing_attributes)}."
                ),
                "missing": missing_attributes,
            },
        )
    digest = hashlib.sha256(content).hexdigest()
    duplicate = next(
        (
            row
            for row in active_attachments(db, ctx, package)
            if row.kind == kind and row.sha256 == digest
        ),
        None,
    )
    if duplicate is not None:
        raise _conflict(
            "attachment_duplicate",
            "That exact file is already attached to this return.",
            attachment_id=str(duplicate.id),
        )

    from app.services.ingestion import bank_slug  # noqa: PLC0415 - avoid an import cycle

    slug = bank_slug(db, bank)
    storage.ensure_institution(slug)
    extension = ALLOWED_MEDIA[media_type]
    object_path = (
        f"bog_returns/{package.reporting_date.isoformat()}/{package.id}"
        f"/attachments/{digest}.{extension}"
    )
    stored = storage.write(
        StorageLocation(slug, _STORAGE_TIER, object_path),
        BytesIO(content),
        ObjectMetadata(
            institution_slug=slug,
            tier=_STORAGE_TIER,
            checksum_sha256=digest,
            written_at=utc_now(),
            written_by=WRITTEN_BY,
            as_of_date=package.reporting_date.isoformat(),
            source_reference=package.return_code,
        ),
        content_type=media_type,
    )
    row = RegulatoryPackageAttachment(
        organization_id=ctx.organization_id,
        bank_id=package.bank_id,
        package_id=package.id,
        package_version=package.version,
        kind=kind,
        title=title,
        original_filename=filename[:255],
        media_type=media_type,
        byte_size=len(content),
        sha256=digest,
        storage_tier=_STORAGE_TIER,
        object_path=object_path,
        storage_version_id=stored.version_id,
        source="package_upload",
        source_attachment_id=None,
        gate=allowed[kind],
        attributes=dict(attributes or {}),
        attached_by=actor_user_id,
    )
    db.add(row)
    db.flush()
    record_event(
        db,
        ctx,
        event_type="regulatory_package.attachment_uploaded",
        entity_type="regulatory_package",
        entity_id=package.id,
        details={
            "attachment_id": str(row.id),
            "return_code": package.return_code,
            "kind": kind,
            "sha256": digest,
            "byte_size": len(content),
            "media_type": media_type,
        },
    )
    db.commit()
    return _read(row, None)


def withdraw_package_attachment(
    db: Session,
    ctx: TenantContext,
    package: RegulatoryPackage,
    attachment_id: UUID,
    *,
    reason: str,
) -> PackageAttachmentRead:
    """Record that a document should not have been filed. Never an edit."""
    actor_user_id = ctx.actor_user_id
    if actor_user_id is None:  # pragma: no cover - refused upstream
        raise _conflict("actor_required", "This action requires a signed-in user.")
    row = db.scalar(
        select(RegulatoryPackageAttachment).where(
            RegulatoryPackageAttachment.organization_id == ctx.organization_id,
            RegulatoryPackageAttachment.package_id == package.id,
            RegulatoryPackageAttachment.id == attachment_id,
        )
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
    if package.status not in ATTACHABLE_STATUSES:
        raise _conflict(
            "package_not_attachable",
            "This return has been submitted; what was filed with it cannot be withdrawn.",
            status=package.status,
        )
    existing = _withdrawals(db, ctx, package).get(row.id)
    if existing is not None:
        return _read(row, existing)
    withdrawal = RegulatoryPackageAttachmentWithdrawal(
        organization_id=ctx.organization_id,
        bank_id=package.bank_id,
        package_id=package.id,
        attachment_id=row.id,
        reason=reason,
        withdrawn_by=actor_user_id,
    )
    db.add(withdrawal)
    db.flush()
    record_event(
        db,
        ctx,
        event_type="regulatory_package.attachment_withdrawn",
        entity_type="regulatory_package",
        entity_id=package.id,
        details={
            "attachment_id": str(row.id),
            "return_code": package.return_code,
            "kind": row.kind,
            "reason": reason,
        },
    )
    db.commit()
    return _read(row, withdrawal)


def copy_freeze_attachments(
    db: Session,
    ctx: TenantContext,
    package: RegulatoryPackage,
    *,
    cycle_id: UUID,
    gates: frozenset[str] = frozenset({"freeze", "optional"}),
) -> list[RegulatoryPackageAttachment]:
    """Carry an ICAAP cycle's evidence onto the package it froze into.

    By REFERENCE: the object path and sha256 are copied, the bytes are not. The
    cycle's upload table is unalterable, so the object cannot change under the
    filing, and the hash is what proves the filed document is that document. No
    commit — the freeze owns the transaction (P3-DESIGN §3.6).
    """
    from app.models.icaap import IcaapAttachment, IcaapAttachmentWithdrawal  # noqa: PLC0415
    from app.services.icaap import guards  # noqa: PLC0415 - framework gates live there

    actor_user_id = ctx.actor_user_id
    if actor_user_id is None:  # pragma: no cover - refused upstream
        raise _conflict("actor_required", "This action requires a signed-in user.")
    withdrawn = set(
        db.scalars(
            select(IcaapAttachmentWithdrawal.attachment_id).where(
                IcaapAttachmentWithdrawal.organization_id == ctx.organization_id,
                IcaapAttachmentWithdrawal.cycle_id == cycle_id,
            )
        )
    )
    cycle_rows = [
        row
        for row in db.scalars(
            select(IcaapAttachment)
            .where(
                IcaapAttachment.organization_id == ctx.organization_id,
                IcaapAttachment.cycle_id == cycle_id,
            )
            .order_by(IcaapAttachment.created_at.asc())
        )
        if row.id not in withdrawn
    ]
    gate_by_kind = _cycle_attachment_gates(db, ctx, cycle_id, guards=guards)
    copied: list[RegulatoryPackageAttachment] = []
    for row in cycle_rows:
        gate = gate_by_kind.get(row.kind, "optional")
        if gate not in gates:
            continue
        copied.append(
            RegulatoryPackageAttachment(
                organization_id=ctx.organization_id,
                bank_id=package.bank_id,
                package_id=package.id,
                package_version=package.version,
                kind=row.kind,
                title=row.title,
                original_filename=row.original_filename,
                media_type=row.media_type,
                byte_size=row.byte_size,
                sha256=row.sha256,
                storage_tier=row.storage_tier,
                object_path=row.object_path,
                storage_version_id=row.storage_version_id,
                source="icaap_cycle",
                source_attachment_id=row.id,
                gate=gate if gate in PACKAGE_ATTACHMENT_GATES else "optional",
                attributes={},
                attached_by=actor_user_id,
            )
        )
    for entry in copied:
        db.add(entry)
    if copied:
        db.flush()
    return copied


def _cycle_attachment_gates(
    db: Session, ctx: TenantContext, cycle_id: UUID, *, guards: Any
) -> dict[str, str]:
    """``{kind: gate}`` from the cycle's own framework, or ``{}`` if unresolvable."""
    from app.models.icaap import IcaapCycle  # noqa: PLC0415

    cycle = db.scalar(
        select(IcaapCycle).where(
            IcaapCycle.organization_id == ctx.organization_id, IcaapCycle.id == cycle_id
        )
    )
    if cycle is None:  # pragma: no cover - the freeze already resolved it
        return {}
    framework = guards.require_framework(cycle)
    return {entry.kind: entry.gate for entry in framework.attachments}


__all__ = [
    "ATTACHABLE_STATUSES",
    "SUPPORTING_DOCUMENT",
    "active_attachments",
    "active_counts",
    "copy_freeze_attachments",
    "ensure_required_attachments",
    "list_attachments",
    "manifest",
    "required_attachments",
    "requirement_rows",
    "upload_package_attachment",
    "withdraw_package_attachment",
]
