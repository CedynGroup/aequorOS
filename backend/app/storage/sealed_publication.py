"""Publish a sealed document once, under an identity a restore cannot move.

A sealed document is published through exactly one code path: the bytes are
written create-only at ``sealed/v1/<catalogue uuid>``, authenticated under the
path the owning feature also records, catalogued with the plaintext hash and
length, and bound to the source row in the same transaction. A retry carrying
the same identity and the same bytes returns the existing publication; different
bytes at that identity are refused rather than overwritten.

The catalogue row is what a reader trusts. The provider version ID is recorded
as write-time provenance and never becomes the read identity.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.db.base import utc_now
from app.storage.client import (
    ObjectMetadata,
    SealedPublication,
    StorageClient,
    StorageLocation,
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
from app.storage.sealed_documents import DocumentBinding, DocumentFamily

_BINDINGS = {
    DocumentFamily.ARTIFACT: ArtifactDocumentBinding,
    DocumentFamily.PACKAGE_ATTACHMENT: PackageAttachmentDocumentBinding,
    DocumentFamily.ICAAP_ATTACHMENT: IcaapAttachmentDocumentBinding,
    DocumentFamily.SUPERVISORY_LETTER: SupervisoryLetterDocumentBinding,
}


@dataclass(frozen=True)
class PublicationRequest:
    """Everything the catalogue records about one sealed document.

    ``context_path`` is the document path the owning feature also records on its
    own row; the bank-key context binds it, while the protected bytes live under
    the immutable key. It is not a physical locator.
    """

    organization_id: str
    bank_id: str
    storage_slug: str
    context_path: str
    payload: bytes
    content_type: str
    published_by: UUID
    as_of_date: str | None = None
    source_reference: str | None = None
    schema_version: str | None = None


@dataclass(frozen=True)
class PublishedDocument:
    """The catalogued identity plus the provider version recorded as provenance."""

    document: SealedDocumentObject
    version_id: str | None

    @property
    def sha256(self) -> str:
        return self.document.sha256

    @property
    def byte_length(self) -> int:
        return self.document.byte_length


def publish_sealed_document(
    db: Session,
    storage: StorageClient,
    request: PublicationRequest,
    *,
    binding: DocumentBinding | None = None,
    object_id: UUID | None = None,
) -> PublishedDocument:
    """Publish the bytes, catalogue them, and bind the row that references them.

    ``object_id`` is the retry identity: a caller continuing an interrupted
    publication passes the identity it already allocated, and the same bytes
    under it are an idempotent success. A new publication omits it.
    """
    digest = hashlib.sha256(request.payload).hexdigest()
    identity = object_id or uuid4()
    existing = db.scalar(
        select(SealedDocumentObject).where(
            SealedDocumentObject.id == identity,
            SealedDocumentObject.organization_id == request.organization_id,
            SealedDocumentObject.bank_id == request.bank_id,
        )
    )
    if existing is not None:
        _require_same_document(existing, request, digest)
        _bind(db, binding, request, existing)
        return PublishedDocument(existing, existing.original_version_id)

    physical = StorageLocation(request.storage_slug, "outputs", sealed_object_key(identity))
    authenticated = StorageLocation(request.storage_slug, "outputs", request.context_path)
    publication: SealedPublication = storage.for_session(db).write_sealed(
        physical,
        io.BytesIO(request.payload),
        ObjectMetadata(
            institution_slug=request.storage_slug,
            tier="outputs",
            checksum_sha256=digest,
            written_at=utc_now(),
            written_by=str(request.published_by),
            as_of_date=request.as_of_date,
            schema_version=request.schema_version,
            source_reference=request.source_reference,
        ),
        authenticated,
        request.content_type,
    )
    descriptor = publication.descriptor
    if descriptor.location != physical or descriptor.metadata.checksum_sha256 != digest:
        raise StorageValidationError("Sealed publication did not store the expected object.")
    insert = sqlite_insert if db.get_bind().dialect.name == "sqlite" else pg_insert
    _ = db.execute(
        insert(SealedDocumentObject)
        .values(
            id=identity,
            organization_id=request.organization_id,
            bank_id=request.bank_id,
            storage_slug=request.storage_slug,
            storage_tier="outputs",
            physical_key=physical.object_path,
            sha256=digest,
            byte_length=len(request.payload),
            context_format=publication.context_format,
            context_path=request.context_path,
            envelope_id=publication.envelope_id,
            original_version_id=descriptor.version_id,
            published_by=request.published_by,
        )
        .on_conflict_do_nothing(index_elements=["id"])
    )
    document = db.scalar(
        select(SealedDocumentObject).where(
            SealedDocumentObject.id == identity,
            SealedDocumentObject.organization_id == request.organization_id,
            SealedDocumentObject.bank_id == request.bank_id,
        )
    )
    if document is None:
        raise StorageValidationError("Sealed publication identity belongs to another scope.")
    _require_same_document(document, request, digest)
    _bind(db, binding, request, document)
    return PublishedDocument(document, document.original_version_id)


def bind_existing_document(
    db: Session,
    request: PublicationRequest,
    document: SealedDocumentObject,
    *,
    binding: DocumentBinding,
) -> PublishedDocument:
    """Bind a second source row to an already-published representation.

    A clone or a freeze carries evidence by reference: the copied row records the
    same path, hash and length, so it reuses the same immutable identity instead
    of publishing a second copy of the same bytes.
    """
    digest = hashlib.sha256(request.payload).hexdigest()
    _require_same_document(document, request, digest)
    bind_sealed_document(
        db,
        binding,
        document=document,
        organization_id=request.organization_id,
        bank_id=request.bank_id,
        bound_by=request.published_by,
    )
    return PublishedDocument(document, document.original_version_id)


def bind_sealed_document(  # noqa: PLR0913 - a binding is its full identity
    db: Session,
    binding: DocumentBinding,
    *,
    document: SealedDocumentObject,
    organization_id: str,
    bank_id: str,
    bound_by: UUID,
) -> None:
    """Record that a source row carries this catalogued representation.

    The source row's own recorded evidence is checked against the catalogue by
    the database binding trigger, so a copied row is accepted only when it
    describes the same bytes, path, tier and original provider version.
    """
    model = _BINDINGS[binding.family]
    insert = sqlite_insert if db.get_bind().dialect.name == "sqlite" else pg_insert
    _ = db.execute(
        insert(model)
        .values(
            source_id=binding.source_id,
            organization_id=organization_id,
            bank_id=bank_id,
            object_id=document.id,
            bound_by=bound_by,
        )
        .on_conflict_do_nothing(index_elements=["source_id"])
    )
    existing = db.scalar(
        select(model.object_id).where(
            model.source_id == binding.source_id,
            model.organization_id == organization_id,
            model.bank_id == bank_id,
        )
    )
    if existing != document.id:
        raise StorageValidationError("Sealed document is already bound to another object.")


def _require_same_document(
    document: SealedDocumentObject, request: PublicationRequest, digest: str
) -> None:
    if (
        document.organization_id != request.organization_id
        or document.bank_id != request.bank_id
        or document.sha256 != digest
        or document.byte_length != len(request.payload)
        or document.storage_slug != request.storage_slug
        or document.context_path != request.context_path
    ):
        raise StorageValidationError(
            "Sealed publication refused: this identity already records other content."
        )


def _bind(
    db: Session,
    binding: DocumentBinding | None,
    request: PublicationRequest,
    document: SealedDocumentObject,
) -> None:
    if binding is None:
        return
    bind_sealed_document(
        db,
        binding,
        document=document,
        organization_id=request.organization_id,
        bank_id=request.bank_id,
        bound_by=request.published_by,
    )
