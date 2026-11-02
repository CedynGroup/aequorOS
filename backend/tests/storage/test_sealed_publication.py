"""One code path writes a sealed document: create-only, catalogued, then bound.

The properties this file pins are the ones a restore, a retry or a concurrent
caller can break: the bytes are written once at an identity a later write cannot
move, the catalogue records the plaintext hash and length a reader will trust,
a repeat of the same publication is a success and a different payload at the
same identity is a refusal, and the provider version is provenance rather than
the read identity.

Runs against the real S3-compatible client over an encrypted bank bucket, so the
create-only conditional write, the bank-key context and the verified read are
all the shipping implementations rather than fakes.
"""

import hashlib
import io
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.tenancy import TenantContext
from app.storage.client import (
    StorageAccessError,
    StorageError,
    StorageLocation,
    StorageValidationError,
    sealed_object_key,
)
from app.storage.models import PackageAttachmentDocumentBinding, SealedDocumentObject
from app.storage.public import (
    DocumentBinding,
    DocumentFamily,
    PublicationRequest,
    RecordedDocument,
    publish_sealed_document,
    resolve_document,
    resolve_sealed_document,
)
from tests.storage.contract import metadata_for
from tests.storage.test_bank_encryption import SLUG, BankStorage, bank_storage
from tests.storage.test_sealed_documents import BANK, decision
from tests.support.helpers import ORG_1, USER_1
from tests.support.sealed_documents import insert_source

__all__ = ["bank_storage"]

CTX = TenantContext(ORG_1, USER_1, authorization_version=1)
PAYLOAD = b"%PDF-1.7\nsynthetic package evidence\n%%EOF\n"
OTHER = b"%PDF-1.7\nreplaced evidence\n%%EOF\n"
CONTEXT_PATH = "bog_returns/2026-03-31/attachments/evidence.pdf"


def _request(**overrides: object) -> PublicationRequest:
    values: dict[str, object] = {
        "organization_id": ORG_1,
        "bank_id": BANK,
        "storage_slug": SLUG,
        "context_path": CONTEXT_PATH,
        "payload": PAYLOAD,
        "content_type": "application/pdf",
        "published_by": USER_1,
        "as_of_date": "2026-03-31",
        "source_reference": "LCR-NSFR",
    }
    values.update(overrides)
    return PublicationRequest(**values)  # type: ignore[arg-type]


def _source(db: Session, payload: bytes = PAYLOAD) -> DocumentBinding:
    """A package attachment row carrying exactly the bytes about to be published."""
    identifier = insert_source(
        db.connection(),
        DocumentFamily.PACKAGE_ATTACHMENT,
        ORG_1,
        BANK,
        hashlib.sha256(payload).hexdigest(),
        len(payload),
        CONTEXT_PATH,
        version="original-provider-version",
    )
    return DocumentBinding(DocumentFamily.PACKAGE_ATTACHMENT, identifier)


def _catalogue(db: Session) -> list[SealedDocumentObject]:
    return list(db.scalars(select(SealedDocumentObject)))


def _publish(
    db: Session,
    storage: BankStorage,
    payload: bytes = PAYLOAD,
    *,
    binding: DocumentBinding | None = None,
    object_id: UUID | None = None,
) -> SealedDocumentObject:
    published = publish_sealed_document(
        db,
        storage.storage,
        _request(payload=payload),
        binding=binding,
        object_id=object_id,
    )
    return published.document


def _overwrite(bank_storage: BankStorage, document: SealedDocumentObject, payload: bytes) -> None:
    """Replace the object's bytes underneath the catalogue, as corruption would.

    Written straight to the bucket: the client refuses the sealed prefix, which
    is the point — the corruption this simulates did not come through the client.
    """
    location = StorageLocation(document.storage_slug, "outputs", document.physical_key)
    _ = bank_storage.s3.put_object(
        Bucket=location.bucket_name("dev"),
        Key=location.object_path,
        Body=payload,
        Metadata=metadata_for(document.storage_slug, "outputs", payload).to_object_metadata(),
    )


def test_a_publication_is_catalogued_once_and_resolves_byte_identically(
    bank_storage: BankStorage, db_session: Session
) -> None:
    """publish → catalogue → resolve → the same bytes, under one identity."""
    binding = _source(db_session)

    published = publish_sealed_document(
        db_session, bank_storage.storage, _request(), binding=binding
    )
    db_session.commit()

    [document] = _catalogue(db_session)
    assert published.document.id == document.id
    assert document.sha256 == hashlib.sha256(PAYLOAD).hexdigest()
    assert document.byte_length == len(PAYLOAD)
    assert document.context_path == CONTEXT_PATH
    assert document.physical_key == sealed_object_key(document.id)
    assert document.storage_tier == "outputs"
    # The bank encryption context authenticated the path the feature records; the
    # protected bytes live under the immutable key.
    assert document.context_format == "aws-esdk-object-v1"
    assert isinstance(document.envelope_id, UUID)

    _descriptor, stream = resolve_sealed_document(
        db_session, CTX, BANK, binding, bank_storage.storage, authorization=decision(binding.family)
    )
    with stream:
        assert stream.read() == PAYLOAD


def test_the_same_publication_twice_keeps_one_identity_and_one_object(
    bank_storage: BankStorage, db_session: Session
) -> None:
    """A retry after a rollback must not mint a second copy of the same document."""
    identity = uuid4()

    first = _publish(db_session, bank_storage, binding=_source(db_session), object_id=identity)
    second = _publish(db_session, bank_storage, object_id=identity)
    db_session.commit()

    assert first.id == second.id == identity
    assert len(_catalogue(db_session)) == 1
    assert len(list(bank_storage.storage.list(SLUG, "outputs"))) == 1


def test_a_second_publication_with_other_content_at_one_identity_is_refused(
    bank_storage: BankStorage, db_session: Session
) -> None:
    """Different bytes at one identity are a conflict, never an overwrite."""
    identity = uuid4()
    _ = _publish(db_session, bank_storage, object_id=identity)

    with pytest.raises(StorageValidationError, match="other content"):
        _publish(db_session, bank_storage, payload=OTHER, object_id=identity)
    [document] = _catalogue(db_session)
    assert document.sha256 == hashlib.sha256(PAYLOAD).hexdigest()


def test_the_store_refuses_a_conditional_write_the_key_already_holds(
    bank_storage: BankStorage,
) -> None:
    """Create-only is the provider's conditional, not a read-then-write in our code.

    The second writer is a *different* publication attempt at an occupied key:
    the store answers PreconditionFailed, and the client converts that into the
    same content comparison, so the answer is the same either way.
    """
    location = StorageLocation(SLUG, "outputs", sealed_object_key(uuid4()))
    first = bank_storage.storage.write_sealed(
        location,
        io.BytesIO(PAYLOAD),
        metadata_for(SLUG, "outputs", PAYLOAD),
        StorageLocation(SLUG, "outputs", CONTEXT_PATH),
    )
    assert first.descriptor.metadata.checksum_sha256 == hashlib.sha256(PAYLOAD).hexdigest()

    with pytest.raises(StorageValidationError, match="other content"):
        bank_storage.storage.write_sealed(
            location,
            io.BytesIO(OTHER),
            metadata_for(SLUG, "outputs", OTHER),
            StorageLocation(SLUG, "outputs", CONTEXT_PATH),
        )


def test_a_bound_document_ignores_the_provider_version_the_row_remembers(
    bank_storage: BankStorage, db_session: Session
) -> None:
    """The catalogue is the identity; a stale or repointed pin never becomes a read."""
    binding = _source(db_session)
    _ = _publish(db_session, bank_storage, binding=binding)
    db_session.commit()

    stale = RecordedDocument(
        location=StorageLocation(SLUG, "outputs", CONTEXT_PATH),
        version_id="a-version-that-is-not-the-published-one",
        sha256="0" * 64,
        byte_length=len(PAYLOAD),
    )
    _descriptor, stream = resolve_document(
        db_session,
        CTX,
        BANK,
        binding,
        bank_storage.storage,
        authorization=decision(binding.family),
        recorded=stale,
    )
    with stream:
        assert stream.read() == PAYLOAD


def test_a_row_published_before_stable_references_resolves_through_its_own_pin(
    bank_storage: BankStorage, db_session: Session
) -> None:
    """No binding: the recorded locator, hash and length are the whole proof."""
    location = StorageLocation(SLUG, "outputs", CONTEXT_PATH)
    stored = bank_storage.storage.write(
        location, io.BytesIO(PAYLOAD), metadata_for(SLUG, "outputs", PAYLOAD)
    )
    binding = _source(db_session)

    _descriptor, stream = resolve_document(
        db_session,
        CTX,
        BANK,
        binding,
        bank_storage.storage,
        authorization=decision(binding.family),
        recorded=RecordedDocument(
            location=location,
            version_id=stored.version_id,
            sha256=hashlib.sha256(PAYLOAD).hexdigest(),
            byte_length=len(PAYLOAD),
        ),
    )
    with stream:
        assert stream.read() == PAYLOAD


def test_a_recorded_identity_that_the_bytes_do_not_match_is_refused(
    bank_storage: BankStorage, db_session: Session
) -> None:
    """An unbound row's pin is a proof obligation, not a suggestion."""
    location = StorageLocation(SLUG, "outputs", CONTEXT_PATH)
    stored = bank_storage.storage.write(
        location, io.BytesIO(PAYLOAD), metadata_for(SLUG, "outputs", PAYLOAD)
    )
    binding = _source(db_session)

    with pytest.raises(StorageValidationError, match="checksum"):
        resolve_document(
            db_session,
            CTX,
            BANK,
            binding,
            bank_storage.storage,
            authorization=decision(binding.family),
            recorded=RecordedDocument(
                location=location,
                version_id=stored.version_id,
                sha256="0" * 64,
                byte_length=len(PAYLOAD),
            ),
        )


def test_a_published_document_whose_object_was_corrupted_is_refused(
    bank_storage: BankStorage, db_session: Session
) -> None:
    """The descriptor and the plaintext digest are both checked, every read."""
    binding = _source(db_session)
    document = _publish(db_session, bank_storage, binding=binding)
    db_session.commit()
    _overwrite(bank_storage, document, OTHER)

    with pytest.raises(StorageError):
        resolve_sealed_document(
            db_session,
            CTX,
            BANK,
            binding,
            bank_storage.storage,
            authorization=decision(binding.family),
        )


def test_the_sealed_namespace_refuses_generic_writes_and_deletes(
    bank_storage: BankStorage, db_session: Session
) -> None:
    """The immutable prefix is a storage contract, so maintenance cannot reach it."""
    binding = _source(db_session)
    identity = uuid4()
    document = _publish(db_session, bank_storage, binding=binding, object_id=identity)
    db_session.commit()
    sealed = StorageLocation(SLUG, "outputs", sealed_object_key(identity))

    with pytest.raises(StorageValidationError, match="immutable"):
        bank_storage.storage.write(sealed, io.BytesIO(OTHER), metadata_for(SLUG, "outputs", OTHER))
    with pytest.raises(StorageValidationError, match="immutable"):
        bank_storage.storage.delete(sealed)
    # The publication is untouched: the same identity still resolves its bytes.
    _descriptor, stream = resolve_sealed_document(
        db_session, CTX, BANK, binding, bank_storage.storage, authorization=decision(binding.family)
    )
    with stream:
        assert stream.read() == PAYLOAD
    assert document.physical_key == sealed.object_path


def test_a_bound_row_carries_the_catalogued_object_and_not_a_second_copy(
    bank_storage: BankStorage, db_session: Session
) -> None:
    """One binding row per source row, and it names the published identity."""
    binding = _source(db_session)
    published = publish_sealed_document(
        db_session, bank_storage.storage, _request(), binding=binding
    )
    db_session.commit()

    bound = db_session.scalar(
        select(PackageAttachmentDocumentBinding.object_id).where(
            PackageAttachmentDocumentBinding.source_id == binding.source_id
        )
    )
    assert bound == published.document.id
    assert db_session.scalar(select(func.count()).select_from(SealedDocumentObject)) == 1


def test_an_impersonated_session_cannot_resolve_a_published_document(
    bank_storage: BankStorage, db_session: Session
) -> None:
    """Publishing never outlives the authority that reads it back."""
    binding = _source(db_session)
    _ = _publish(db_session, bank_storage, binding=binding)
    db_session.commit()

    impersonated = TenantContext(
        ORG_1, USER_1, authorization_version=1, impersonation_context="INSPECTION-1"
    )
    with pytest.raises(StorageAccessError):
        resolve_sealed_document(
            db_session,
            impersonated,
            BANK,
            binding,
            bank_storage.storage,
            authorization=decision(binding.family),
        )
