"""Stable reads preserve original bank keys and verify before returning bytes."""

import io
from dataclasses import replace
from typing import BinaryIO
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.authorization import (
    AuthorizationDecision,
    InstitutionScope,
    Module,
    Permission,
    PrincipalLocator,
    PrincipalType,
    ResourceLocator,
    Sensitivity,
)
from app.core.key_management.types import KeyStatus, KeyUnavailableError
from app.core.tenancy import TenantContext
from app.storage.client import (
    SealedEncryptionContext,
    StorageAccessError,
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
from app.storage.public import DocumentBinding, DocumentFamily, resolve_sealed_document
from tests.storage.contract import metadata_for
from tests.storage.test_bank_encryption import KEY, SLUG, BankStorage, bank_storage
from tests.support.helpers import ORG_1, ORG_2, USER_1
from tests.support.inmemory_storage import InMemoryStorageClient
from tests.support.sealed_documents import insert_source

__all__ = ["bank_storage"]
BANK = "BK-SAMP0001"
CTX = TenantContext(ORG_1, USER_1, authorization_version=1)
MODELS = {
    DocumentFamily.ARTIFACT: ArtifactDocumentBinding,
    DocumentFamily.PACKAGE_ATTACHMENT: PackageAttachmentDocumentBinding,
    DocumentFamily.ICAAP_ATTACHMENT: IcaapAttachmentDocumentBinding,
    DocumentFamily.SUPERVISORY_LETTER: SupervisoryLetterDocumentBinding,
}


def decision(
    family: DocumentFamily, ctx: TenantContext = CTX, bank: str = BANK
) -> AuthorizationDecision:
    assert ctx.actor_user_id is not None
    module = (
        Module.REGULATORY
        if family in (DocumentFamily.ARTIFACT, DocumentFamily.PACKAGE_ATTACHMENT)
        else Module.CAPITAL
    )
    return AuthorizationDecision(
        True,
        "scoped authorization allowed",
        Permission.VIEW,
        PrincipalLocator(ctx.organization_id, ctx.actor_user_id, PrincipalType.HUMAN),
        ResourceLocator(
            ctx.organization_id,
            InstitutionScope.INSTITUTION,
            bank,
            module,
            Sensitivity.CONFIDENTIAL,
        ),
        (uuid4(),),
        (),
        (),
    )


def catalogue(  # noqa: PLR0913 - synthetic catalogue descriptor
    db: Session,
    family: DocumentFamily,
    payload: bytes,
    *,
    envelope: UUID | None = None,
    length: int | None = None,
    sha256: str | None = None,
) -> tuple[DocumentBinding, SealedDocumentObject]:
    identifier = uuid4()
    metadata = metadata_for(SLUG, "outputs", payload)
    digest, size = (
        sha256 or metadata.checksum_sha256,
        length if length is not None else len(payload),
    )
    source = insert_source(db.connection(), family, ORG_1, BANK, digest, size, "legacy/archive.pdf")
    document = SealedDocumentObject(
        id=identifier,
        organization_id=ORG_1,
        bank_id=BANK,
        storage_slug=SLUG,
        storage_tier="outputs",
        physical_key=f"sealed/v1/{identifier}",
        sha256=digest,
        byte_length=size,
        context_format="aws-esdk-object-v1" if envelope else "plaintext-v1",
        context_path="legacy/archive.pdf",
        envelope_id=envelope,
        original_version_id="original-provider-version",
        published_by=USER_1,
    )
    db.add(document)
    db.flush()
    db.add(
        MODELS[family](
            source_id=source,
            organization_id=ORG_1,
            bank_id=BANK,
            object_id=identifier,
            bound_by=USER_1,
        )
    )
    db.flush()
    return DocumentBinding(family, source), document


class RepresentationStorage(InMemoryStorageClient):
    """Boundary fake with a readable plaintext representation."""

    calls: int = 0
    source: BinaryIO | None = None

    def read_sealed(
        self, location: StorageLocation, context: SealedEncryptionContext
    ) -> tuple[StorageObject, BinaryIO]:
        self.calls += 1
        descriptor, body = self.read(location)
        self.source = body
        return descriptor, body


@pytest.mark.parametrize("family", list(DocumentFamily))
@pytest.mark.parametrize("fault", [None, "hash", "short", "long"])
def test_complete_plaintext_is_verified_before_release(
    bank_storage: BankStorage,
    db_session: Session,
    family: DocumentFamily,
    fault: str | None,
) -> None:
    payload = b"synthetic sealed bytes" * 700_000
    expected_length = len(payload) + (1 if fault == "short" else -1 if fault == "long" else 0)
    binding, document = catalogue(
        db_session,
        family,
        payload,
        length=expected_length,
        sha256="0" * 64 if fault == "hash" else None,
    )
    storage = RepresentationStorage()
    location = StorageLocation(SLUG, "outputs", document.physical_key)
    storage.write(location, io.BytesIO(payload), metadata_for(SLUG, "outputs", payload))
    if fault:
        with pytest.raises(
            StorageValidationError, match="checksum" if fault == "hash" else "length"
        ):
            resolve_sealed_document(
                db_session, CTX, BANK, binding, storage, authorization=decision(family)
            )
    else:
        _, body = resolve_sealed_document(
            db_session, CTX, BANK, binding, storage, authorization=decision(family)
        )
        with body:
            assert body.tell() == 0
            assert body.read() == payload
            assert body.fileno() >= 0  # Large verified content has a disk-backed spool.
    assert storage.source is not None and storage.source.closed
    assert storage.calls == 1


@pytest.mark.parametrize("family", list(DocumentFamily))
def test_bank_and_tenant_scope_and_missing_binding_refuse_without_storage(
    bank_storage: BankStorage,
    db_session: Session,
    family: DocumentFamily,
) -> None:
    binding, _ = catalogue(db_session, family, b"payload")
    storage = RepresentationStorage()
    sibling = "BK-SIBLING"
    other = replace(CTX, organization_id=ORG_2)
    for ctx, bank in ((CTX, sibling), (other, BANK)):
        with pytest.raises(StorageNotFoundError):
            resolve_sealed_document(
                db_session, ctx, bank, binding, storage, authorization=decision(family, ctx, bank)
            )
    with pytest.raises(StorageNotFoundError):
        resolve_sealed_document(
            db_session,
            CTX,
            BANK,
            DocumentBinding(family, uuid4()),
            storage,
            authorization=decision(family),
        )
    for invalid in (
        replace(decision(family), allowed=False),
        replace(decision(family), matching_binding_ids=()),
        decision(family, CTX, sibling),
    ):
        with pytest.raises(StorageAccessError):
            resolve_sealed_document(db_session, CTX, BANK, binding, storage, authorization=invalid)
    assert storage.calls == 0


def test_legacy_ciphertext_at_new_key_preserves_original_envelope_dependency(
    bank_storage: BankStorage,
    db_session: Session,
) -> None:
    storage, s3 = bank_storage.storage, bank_storage.s3
    payload = b"synthetic encrypted historical revision"
    original = StorageLocation(SLUG, "outputs", "legacy/archive.pdf")
    stored = storage.write(original, io.BytesIO(payload), metadata_for(SLUG, "outputs", payload))
    raw = s3.get_object(Bucket=original.bucket_name("dev"), Key=original.object_path)
    # The storage boundary's response carries ciphertext and its original headers.
    from typing import cast  # noqa: PLC0415

    headers = cast(dict[str, str], raw["Metadata"])
    with cast(BinaryIO, raw["Body"]) as body:
        ciphertext = body.read()
    envelope = UUID(headers["key-envelope-id"])
    family = DocumentFamily.ARTIFACT
    binding, document = catalogue(db_session, family, payload, envelope=envelope)
    restored = s3.put_object(
        Bucket=original.bucket_name("dev"),
        Key=document.physical_key,
        Body=ciphertext,
        Metadata=headers,
        ContentType="application/pdf",
    )
    assert restored["VersionId"] != stored.version_id
    descriptor, verified = resolve_sealed_document(
        db_session, CTX, BANK, binding, storage, authorization=decision(family)
    )
    with verified:
        assert verified.read() == payload
    assert descriptor.location.object_path == document.physical_key
    assert descriptor.version_id == restored["VersionId"]
    assert descriptor.size_bytes == len(payload)
    with pytest.raises(StorageAccessError):
        storage.read_sealed(
            descriptor.location,
            SealedEncryptionContext(
                "aws-esdk-object-v1",
                StorageLocation("foreign-bank", "outputs", original.object_path),
                document.sha256,
                envelope,
            ),
        )
    db_session.commit()  # End the admitted transaction and its bounded key cache.
    bank_storage.provider.set_status(KEY, KeyStatus.DISABLED)
    with pytest.raises(KeyUnavailableError):
        resolve_sealed_document(
            db_session, CTX, BANK, binding, storage, authorization=decision(family)
        )
    bank_storage.provider.set_status(KEY, KeyStatus.ACTIVE)
    # A substituted envelope or foreign-bank descriptor is refused before decrypting.
    forged = dict(headers, **{"key-envelope-id": str(uuid4())})
    s3.put_object(
        Bucket=original.bucket_name("dev"),
        Key=document.physical_key,
        Body=ciphertext,
        Metadata=forged,
        ContentType="application/pdf",
    )
    with pytest.raises(StorageAccessError):
        resolve_sealed_document(
            db_session, CTX, BANK, binding, storage, authorization=decision(family)
        )
    assert db_session.scalar(select(SealedDocumentObject.envelope_id)) == envelope


def test_plaintext_descriptor_does_not_bypass_connected_bank_key(
    bank_storage: BankStorage,
    db_session: Session,
) -> None:
    family = DocumentFamily.ARTIFACT
    payload = b"synthetic plaintext"
    binding, document = catalogue(db_session, family, payload)
    bank_storage.s3.put_object(
        Bucket=f"aequoros-dev-{SLUG}-outputs",
        Key=document.physical_key,
        Body=payload,
        Metadata=metadata_for(SLUG, "outputs", payload).to_object_metadata(),
    )
    with pytest.raises(StorageAccessError):
        resolve_sealed_document(
            db_session, CTX, BANK, binding, bank_storage.storage, authorization=decision(family)
        )
