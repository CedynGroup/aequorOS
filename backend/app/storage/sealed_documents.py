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
    InstitutionScope,
    Module,
    Permission,
    PrincipalType,
    Sensitivity,
)
from app.core.tenancy import TenantContext
from app.storage.client import (
    SealedEncryptionContext,
    StorageAccessError,
    StorageClient,
    StorageLocation,
    StorageNotFoundError,
    StorageObject,
    StorageValidationError,
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


def resolve_sealed_document(  # noqa: PLR0913 - explicit authorization and storage boundaries
    db: Session,
    ctx: TenantContext,
    bank_id: str,
    binding: DocumentBinding,
    storage: StorageClient,
    *,
    authorization: AuthorizationDecision,
) -> tuple[StorageObject, BinaryIO]:
    """Return a rewound verified file; the caller owns and must close it.

    The owning feature must supply a fresh, whole-institution VIEW decision from
    scoped authorization (including its workflow/presentation checks) in this
    request/session. This kernel never infers authority from a scalar role or a
    token claim. A binding is mandatory: absence is an error, never a legacy-read
    or provider-version fallback. No consumer is converted by defining this API.
    """
    module = (
        Module.REGULATORY
        if binding.family in (DocumentFamily.ARTIFACT, DocumentFamily.PACKAGE_ATTACHMENT)
        else Module.CAPITAL
    )
    resource = authorization.resource
    principal = authorization.principal
    if (
        not authorization.allowed
        or not authorization.matching_binding_ids
        or authorization.permission is not Permission.VIEW
        or resource.organization_id != ctx.organization_id
        or resource.institution_scope is not InstitutionScope.INSTITUTION
        or resource.institution_id != bank_id
        or resource.module is not module
        or resource.sensitivity not in (Sensitivity.CONFIDENTIAL, Sensitivity.RESTRICTED)
        or principal.organization_id != ctx.organization_id
        or principal.principal_id != ctx.actor_user_id
        or principal.principal_type is not PrincipalType.HUMAN
        or ctx.impersonation_context is not None
        or ctx.authorization_version is None
    ):
        raise StorageAccessError("Bank document authorization is required.")
    model = _BINDINGS[binding.family]
    document = db.scalar(
        select(SealedDocumentObject)
        .join(
            model,
            (model.object_id == SealedDocumentObject.id)
            & (model.organization_id == SealedDocumentObject.organization_id)
            & (model.bank_id == SealedDocumentObject.bank_id),
        )
        .where(
            model.source_id == binding.source_id,
            model.organization_id == ctx.organization_id,
            model.bank_id == bank_id,
            SealedDocumentObject.organization_id == ctx.organization_id,
            SealedDocumentObject.bank_id == bank_id,
        )
    )
    if document is None:
        raise StorageNotFoundError("Sealed document binding not found.")
    if (
        document.storage_tier != "outputs"
        or document.physical_key != f"sealed/v1/{document.id}"
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
    descriptor, source = storage.for_session(db).read_sealed(physical, context)
    verified = SpooledTemporaryFile[bytes](max_size=_SPOOL_BYTES, mode="w+b")
    try:
        with source:
            digest = hashlib.sha256()
            length = 0
            while chunk := source.read(_CHUNK_BYTES):
                length += len(chunk)
                if length > document.byte_length:
                    raise StorageValidationError("Sealed document length mismatch.")
                digest.update(chunk)
                _ = verified.write(chunk)
        if length != document.byte_length:
            raise StorageValidationError("Sealed document length mismatch.")
        if not hmac.compare_digest(digest.hexdigest(), document.sha256):
            raise StorageValidationError("Sealed document checksum mismatch.")
        _ = verified.seek(0)
        return replace(descriptor, size_bytes=length), cast(BinaryIO, verified)
    except BaseException:
        verified.close()
        raise
