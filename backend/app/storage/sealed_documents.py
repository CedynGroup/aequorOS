"""Resolve a bank-authorized binding and verify every byte before release."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass, replace
from enum import StrEnum
from tempfile import SpooledTemporaryFile
from typing import BinaryIO, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.authorization import (
    AuthorizationDecision,
    ExaminerDocumentAuthority,
    InstitutionScope,
    Module,
    Permission,
    PrincipalType,
    Sensitivity,
)
from app.core.return_family_access import FamilyGate, gate_for
from app.core.tenancy import TenantContext
from app.db.base import Base
from app.storage.client import (
    SealedEncryptionContext,
    StorageAccessError,
    StorageClient,
    StorageLocation,
    StorageNotFoundError,
    StorageObject,
    StorageValidationError,
    sealed_object_key,
)
from app.storage.models import (
    ArtifactDocumentBinding,
    IcaapAttachmentDocumentBinding,
    PackageAttachmentDocumentBinding,
    SealedDocumentObject,
    SupervisoryLetterDocumentBinding,
)

_SPOOL_BYTES = 8 * 1024 * 1024
_CHUNK_BYTES = 1024 * 1024


class DocumentFamily(StrEnum):
    ARTIFACT = "artifact"
    PACKAGE_ATTACHMENT = "package_attachment"
    ICAAP_ATTACHMENT = "icaap_attachment"
    SUPERVISORY_LETTER = "supervisory_letter"


_BINDINGS = {
    DocumentFamily.ARTIFACT: ArtifactDocumentBinding,
    DocumentFamily.PACKAGE_ATTACHMENT: PackageAttachmentDocumentBinding,
    DocumentFamily.ICAAP_ATTACHMENT: IcaapAttachmentDocumentBinding,
    DocumentFamily.SUPERVISORY_LETTER: SupervisoryLetterDocumentBinding,
}


@dataclass(frozen=True)
class DocumentBinding:
    family: DocumentFamily
    source_id: UUID


@dataclass(frozen=True)
class RecordedDocument:
    """A sealed row's own legacy locator, used only when no stable binding exists.

    ``version_id`` is the provider pin the row recorded; it is a read token for
    this legacy path alone and never a fallback for a bound document.
    """

    location: StorageLocation
    version_id: str | None
    sha256: str
    byte_length: int


def _source_gate(
    db: Session, ctx: TenantContext, bank_id: str, binding: DocumentBinding
) -> FamilyGate:
    if binding.family not in (DocumentFamily.ARTIFACT, DocumentFamily.PACKAGE_ATTACHMENT):
        return FamilyGate(Module.CAPITAL, Sensitivity.CONFIDENTIAL)
    source_name = (
        "regulatory_artifact_versions"
        if binding.family is DocumentFamily.ARTIFACT
        else "regulatory_package_attachments"
    )
    source = Base.metadata.tables[source_name]
    package = Base.metadata.tables["regulatory_packages"]
    query = (
        select(package.c.return_family)
        .join(
            source,
            (source.c.package_id == package.c.id)
            & (source.c.organization_id == package.c.organization_id),
        )
        .where(
            source.c.id == binding.source_id,
            source.c.organization_id == ctx.organization_id,
            package.c.organization_id == ctx.organization_id,
            package.c.bank_id == bank_id,
        )
    )
    if binding.family is DocumentFamily.PACKAGE_ATTACHMENT:
        query = query.where(source.c.bank_id == bank_id)
    family = cast(str | None, db.scalar(query))
    if family is None:
        raise StorageNotFoundError("Sealed document source not found.")
    return gate_for(family)


def _bound_document(
    db: Session, organization_id: str, bank_id: str, binding: DocumentBinding
) -> SealedDocumentObject | None:
    """The catalogue object this source row is bound to, or None when it predates one."""
    model = _BINDINGS[binding.family]
    return db.scalar(
        select(SealedDocumentObject)
        .join(
            model,
            (model.object_id == SealedDocumentObject.id)
            & (model.organization_id == SealedDocumentObject.organization_id)
            & (model.bank_id == SealedDocumentObject.bank_id),
        )
        .where(
            model.source_id == binding.source_id,
            model.organization_id == organization_id,
            model.bank_id == bank_id,
            SealedDocumentObject.organization_id == organization_id,
            SealedDocumentObject.bank_id == bank_id,
        )
    )


def find_sealed_document(
    db: Session, organization_id: str, bank_id: str, binding: DocumentBinding
) -> SealedDocumentObject | None:
    """The stable identity a source row carries, for a caller copying it by reference.

    Returns None for a row published before stable references; callers preserve
    their legacy behavior for those rows rather than inventing a binding.
    """
    return _bound_document(db, organization_id, bank_id, binding)


def document_retention_reference(  # noqa: PLR0913
    db: Session,
    organization_id: str,
    bank_id: str,
    binding: DocumentBinding,
    recorded_location: StorageLocation,
    recorded_version_id: str | None,
) -> tuple[StorageLocation, str | None]:
    document = _bound_document(db, organization_id, bank_id, binding)
    if document is None:
        return recorded_location, recorded_version_id
    return (
        StorageLocation(document.storage_slug, "outputs", document.physical_key),
        document.original_version_id,
    )


def _authorize(  # noqa: PLR0913 - explicit authorization and storage boundaries
    db: Session,
    ctx: TenantContext,
    bank_id: str,
    binding: DocumentBinding,
    authorization: AuthorizationDecision | ExaminerDocumentAuthority,
) -> None:
    resource = authorization.resource
    if (
        not authorization.allowed
        or resource.organization_id != ctx.organization_id
        or resource.institution_scope is not InstitutionScope.INSTITUTION
        or resource.institution_id != bank_id
    ):
        raise StorageAccessError("Bank document authorization is required.")
    if isinstance(authorization, ExaminerDocumentAuthority):
        if (
            ctx.impersonation_context != authorization.session_id
            or ctx.actor_operator != authorization.operator
            or ctx.actor_user_id is not None
        ):
            raise StorageAccessError("Bank document authorization is required.")
    else:
        principal = authorization.principal
        if (
            not authorization.matching_binding_ids
            or authorization.permission is not Permission.VIEW
            or principal.organization_id != ctx.organization_id
            or principal.principal_id != ctx.actor_user_id
            or principal.principal_type is not PrincipalType.HUMAN
            or ctx.impersonation_context is not None
            or ctx.authorization_version is None
        ):
            raise StorageAccessError("Bank document authorization is required.")
    gate = _source_gate(db, ctx, bank_id, binding)
    if resource.module is not gate.module or resource.sensitivity is not gate.sensitivity:
        raise StorageAccessError("Bank document authorization is required.")


def _verified_spool(source: BinaryIO, *, sha256: str, byte_length: int) -> BinaryIO:
    """Hash every plaintext byte against the recorded identity before release."""
    verified = SpooledTemporaryFile[bytes](max_size=_SPOOL_BYTES, mode="w+b")
    try:
        with source:
            digest = hashlib.sha256()
            length = 0
            while chunk := source.read(_CHUNK_BYTES):
                length += len(chunk)
                if length > byte_length:
                    raise StorageValidationError("Sealed document length mismatch.")
                digest.update(chunk)
                _ = verified.write(chunk)
        if length != byte_length:
            raise StorageValidationError("Sealed document length mismatch.")
        if not hmac.compare_digest(digest.hexdigest(), sha256):
            raise StorageValidationError("Sealed document checksum mismatch.")
        _ = verified.seek(0)
        return cast(BinaryIO, verified)
    except BaseException:
        verified.close()
        raise


def _read_sealed(
    storage: StorageClient, document: SealedDocumentObject
) -> tuple[StorageObject, BinaryIO]:
    if (
        document.storage_tier != "outputs"
        or document.physical_key != sealed_object_key(document.id)
        or document.context_format not in ("aws-esdk-object-v1", "plaintext-v1")
        or len(document.sha256) != 64
        or any(char not in "0123456789abcdef" for char in document.sha256)
        or document.byte_length < 0
        or not document.context_path
    ):
        raise StorageValidationError("Invalid sealed document representation.")
    physical = StorageLocation(document.storage_slug, "outputs", document.physical_key)
    context = SealedEncryptionContext(
        document.context_format,
        StorageLocation(document.storage_slug, "outputs", document.context_path),
        document.sha256,
        document.envelope_id,
    )
    descriptor, source = storage.read_sealed(physical, context)
    return (
        replace(descriptor, size_bytes=document.byte_length),
        _verified_spool(source, sha256=document.sha256, byte_length=document.byte_length),
    )


def _read_recorded(
    storage: StorageClient, recorded: RecordedDocument
) -> tuple[StorageObject, BinaryIO]:
    descriptor, source = storage.read(recorded.location, version_id=recorded.version_id)
    return (
        replace(descriptor, size_bytes=recorded.byte_length),
        _verified_spool(source, sha256=recorded.sha256, byte_length=recorded.byte_length),
    )


def resolve_sealed_document(  # noqa: PLR0913 - explicit authorization and storage boundaries
    db: Session,
    ctx: TenantContext,
    bank_id: str,
    binding: DocumentBinding,
    storage: StorageClient,
    *,
    authorization: AuthorizationDecision | ExaminerDocumentAuthority,
) -> tuple[StorageObject, BinaryIO]:
    """Return a rewound verified file; the caller owns and must close it.

    The owning feature must supply fresh whole-institution VIEW authority from
    scoped authorization or a verified examiner session, including its workflow
    and presentation checks in this request/session. This kernel never infers
    authority from a scalar role or a token claim. A binding is mandatory:
    absence is an error, never a legacy-read or provider-version fallback.
    """
    _authorize(db, ctx, bank_id, binding, authorization)
    document = _bound_document(db, ctx.organization_id, bank_id, binding)
    if document is None:
        raise StorageNotFoundError("Sealed document binding not found.")
    return _read_sealed(storage.for_session(db), document)


def resolve_document(  # noqa: PLR0913 - explicit authorization and storage boundaries
    db: Session,
    ctx: TenantContext,
    bank_id: str,
    binding: DocumentBinding,
    storage: StorageClient,
    *,
    authorization: AuthorizationDecision | ExaminerDocumentAuthority,
    recorded: RecordedDocument,
) -> tuple[StorageObject, BinaryIO]:
    """Read a source row's bytes by its stable identity, else by its own recorded pin.

    ``recorded`` carries the locator, hash and length the source row already
    holds. It is used only for rows published before stable references existed;
    a bound row always resolves through its catalogue identity, so a missing or
    damaged binding never degrades into "read whatever is latest". Both paths
    verify the complete plaintext against the recorded identity before returning
    a rewound file the caller must close.
    """
    _authorize(db, ctx, bank_id, binding, authorization)
    document = _bound_document(db, ctx.organization_id, bank_id, binding)
    session_storage = storage.for_session(db)
    if document is not None:
        return _read_sealed(session_storage, document)
    return _read_recorded(session_storage, recorded)
