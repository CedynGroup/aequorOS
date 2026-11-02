from __future__ import annotations

import hashlib
from dataclasses import replace
from uuid import UUID

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.case.service import documents
from app.integrations.storage.base import StoredObjectHead
from app.models import AuditEvent, Document, DocumentChunk, StoredObject
from tests.case.factories import ServiceFactories, upload_payload


def test_request_upload_validates_content_type_and_size(
    db_session: Session,
    service_factories: ServiceFactories,
) -> None:
    factories = service_factories
    settings = factories.settings
    ctx = factories.ctx
    case = factories.cases.create()

    with pytest.raises(HTTPException) as unsupported:
        documents.request_upload(
            db_session,
            ctx,
            upload_payload(
                case_id=case.id,
                content_type="application/octet-stream",
            ),
            settings=settings,
            storage_client=factories.storage,
        )
    assert unsupported.value.status_code == 400

    with pytest.raises(HTTPException) as oversized:
        documents.request_upload(
            db_session,
            ctx,
            upload_payload(case_id=case.id, byte_size=settings.risk_max_upload_bytes + 1),
            settings=settings,
            storage_client=factories.storage,
        )
    assert oversized.value.status_code == 400


def test_request_upload_creates_scoped_object_key(
    db_session: Session,
    service_factories: ServiceFactories,
) -> None:
    factories = service_factories
    ctx = factories.ctx
    case = factories.cases.create()

    result = factories.documents.request_upload(case.id)

    stored_object = db_session.scalar(
        select(StoredObject).where(StoredObject.organization_id == ctx.organization_id)
    )
    assert stored_object is not None
    assert (
        stored_object.object_key
        == f"orgs/{ctx.organization_id}/documents/{result.document_id}/original"
    )
    assert stored_object.status == "pending_upload"
    assert str(ctx.organization_id) in result.upload_url


def test_complete_upload_persists_storage_metadata(
    db_session: Session,
    service_factories: ServiceFactories,
) -> None:
    factories = service_factories
    case = factories.cases.create()

    document = factories.documents.create_uploaded(case.id)

    stored_object = db_session.get(StoredObject, document.stored_object_id)
    assert stored_object is not None
    assert document.status == "uploaded"
    assert stored_object.status == "available"
    assert stored_object.etag == '"etag"'


def _staged_upload(
    factories: ServiceFactories,
    *,
    declared: bytes,
    stored: bytes | None = None,
    declared_sha256: str | None = None,
) -> tuple[UUID, str | None]:
    """Request an upload for ``declared`` bytes and land ``stored`` instead.

    The gap between the two is the whole point: a presigned PUT accepts any
    body, so completing with different content than was declared must be
    refused rather than recorded.
    """
    case = factories.cases.create()
    upload = factories.documents.request_upload(
        case.id,
        byte_size=len(declared),
        sha256=declared_sha256,
    )
    landed = declared if stored is None else stored
    factories.storage.head = StoredObjectHead(
        content_type="application/pdf",
        byte_size=len(landed),
        etag='"etag"',
    )
    factories.storage.body = landed
    return upload.document_id, declared_sha256


def _complete(factories: ServiceFactories, db: Session, document_id: UUID) -> HTTPException:
    with pytest.raises(HTTPException) as refused:
        documents.complete_upload(
            db,
            factories.ctx,
            document_id,
            settings=factories.settings,
            storage_client=factories.storage,
        )
    return refused.value


def test_complete_upload_refuses_a_stored_digest_that_is_not_a_sha256(
    db_session: Session,
    service_factories: ServiceFactories,
) -> None:
    # A row written before the request schema checked declarations can hold
    # anything; it cannot be shown to match, so it is refused rather than
    # surfaced as a server fault.
    factories = service_factories
    payload = b"legacy row body"
    document_id, _ = _staged_upload(
        factories,
        declared=payload,
        declared_sha256=hashlib.sha256(payload).hexdigest(),
    )
    stored_object = db_session.get(StoredObject, _stored_object_id(db_session, document_id))
    assert stored_object is not None
    stored_object.sha256 = "legacy-not-a-digest"
    db_session.flush()

    refused = _complete(factories, db_session, document_id)

    assert refused.status_code == 400
    assert "checksum" in str(refused.detail)


def _stored_object_id(db_session: Session, document_id: UUID) -> UUID:
    document = db_session.get(Document, document_id)
    assert document is not None
    return document.stored_object_id


def test_complete_upload_accepts_content_matching_the_declared_digest(
    db_session: Session,
    service_factories: ServiceFactories,
) -> None:
    factories = service_factories
    payload = b"%PDF-1.7 declared body"
    document_id, _ = _staged_upload(
        factories,
        declared=payload,
        declared_sha256=hashlib.sha256(payload).hexdigest().upper(),
    )

    document = documents.complete_upload(
        db_session,
        factories.ctx,
        document_id,
        settings=factories.settings,
        storage_client=factories.storage,
    )

    stored_object = db_session.get(StoredObject, document.stored_object_id)
    assert document.status == "uploaded"
    assert stored_object is not None
    # The request schema is what normalises a declaration's spelling; the
    # service compares case-insensitively either way, so an upper-case
    # declaration that matches the content is still accepted.
    assert stored_object.sha256 is not None
    assert stored_object.sha256.lower() == hashlib.sha256(payload).hexdigest()


def test_complete_upload_refuses_content_that_differs_from_the_declared_digest(
    db_session: Session,
    service_factories: ServiceFactories,
) -> None:
    factories = service_factories
    declared = b"%PDF-1.7 declared body"
    # Same length, different content: the size check alone cannot catch this.
    document_id, _ = _staged_upload(
        factories,
        declared=declared,
        stored=b"%PDF-1.7 ATTACKER body",
        declared_sha256=hashlib.sha256(declared).hexdigest(),
    )

    refused = _complete(factories, db_session, document_id)

    assert refused.status_code == 400
    assert "checksum" in str(refused.detail)
    _assert_still_pending(db_session, document_id)


def test_complete_upload_refuses_a_size_that_differs_from_the_declaration(
    db_session: Session,
    service_factories: ServiceFactories,
) -> None:
    factories = service_factories
    document_id, _ = _staged_upload(factories, declared=b"x" * 32, stored=b"x" * 64)

    refused = _complete(factories, db_session, document_id)

    assert refused.status_code == 400
    assert "declared size" in str(refused.detail)
    _assert_still_pending(db_session, document_id)


def test_complete_upload_refuses_when_storage_reports_no_size(
    db_session: Session,
    service_factories: ServiceFactories,
) -> None:
    factories = service_factories
    case = factories.cases.create()
    upload = factories.documents.request_upload(case.id, byte_size=16)
    factories.storage.head = StoredObjectHead(content_type="application/pdf", etag='"etag"')

    refused = _complete(factories, db_session, upload.document_id)

    assert refused.status_code == 400
    assert "size" in str(refused.detail)
    _assert_still_pending(db_session, upload.document_id)


def test_complete_upload_does_not_read_an_object_that_is_already_over_the_cap(
    db_session: Session,
    service_factories: ServiceFactories,
) -> None:
    # The cap exists to bound work. Reading a body that is going to be refused
    # for its size spends the whole read to reach a decision already available
    # from the size alone.
    factories = service_factories
    payload = b"declared body"
    document_id, _ = _staged_upload(
        factories, declared=payload, declared_sha256=hashlib.sha256(payload).hexdigest()
    )
    assert factories.storage.head is not None
    factories.storage.head = replace(
        factories.storage.head, byte_size=factories.settings.risk_max_upload_bytes + 1
    )

    refused = _complete(factories, db_session, document_id)

    assert refused.status_code == 400
    assert "maximum size" in str(refused.detail)
    assert factories.storage.digest_reads == []
    _assert_still_pending(db_session, document_id)


def test_complete_upload_reads_the_object_only_when_a_digest_was_declared(
    db_session: Session,
    service_factories: ServiceFactories,
) -> None:
    factories = service_factories
    payload = b"no digest declared here"
    undeclared_id, _ = _staged_upload(factories, declared=payload)

    documents.complete_upload(
        db_session,
        factories.ctx,
        undeclared_id,
        settings=factories.settings,
        storage_client=factories.storage,
    )

    assert factories.storage.digest_reads == []

    declared_id, _ = _staged_upload(
        factories, declared=payload, declared_sha256=hashlib.sha256(payload).hexdigest()
    )
    documents.complete_upload(
        db_session,
        factories.ctx,
        declared_id,
        settings=factories.settings,
        storage_client=factories.storage,
    )

    assert len(factories.storage.digest_reads) == 1


def test_complete_upload_refuses_a_body_that_outgrows_the_reported_size(
    db_session: Session,
    service_factories: ServiceFactories,
) -> None:
    factories = service_factories
    payload = b"declared"
    document_id, _ = _staged_upload(
        factories, declared=payload, declared_sha256=hashlib.sha256(payload).hexdigest()
    )
    # Storage under-reports the size it observed, so the verification read must
    # stop at the report rather than hash an unbounded stream.
    assert factories.storage.head is not None
    factories.storage.head = replace(factories.storage.head, byte_size=4)

    refused = _complete(factories, db_session, document_id)

    assert refused.status_code in {400}
    _assert_still_pending(db_session, document_id)


def _assert_still_pending(db_session: Session, document_id: UUID) -> None:
    db_session.rollback()
    document = db_session.get(Document, document_id)
    assert document is not None
    assert document.status == "upload_requested"
    stored_object = db_session.get(StoredObject, document.stored_object_id)
    assert stored_object is not None
    assert stored_object.status == "pending_upload"


def test_parse_rejects_unuploaded_and_deleted_documents(
    db_session: Session,
    service_factories: ServiceFactories,
) -> None:
    factories = service_factories
    ctx = factories.ctx
    case = factories.cases.create()
    upload = factories.documents.request_upload(case.id)

    with pytest.raises(HTTPException) as unuploaded:
        documents.request_parse(db_session, ctx, upload.document_id)
    assert unuploaded.value.status_code == 400

    document = factories.documents.create_uploaded(case.id)
    documents.delete_document(
        db_session,
        ctx,
        document.id,
    )

    with pytest.raises(HTTPException) as deleted:
        documents.request_parse(db_session, ctx, document.id)
    assert deleted.value.status_code == 409


def test_parse_stub_creates_chunks_idempotently(
    db_session: Session,
    service_factories: ServiceFactories,
) -> None:
    factories = service_factories
    ctx = factories.ctx
    case = factories.cases.create()
    document = factories.documents.create_uploaded(case.id)

    first = documents.request_parse(db_session, ctx, document.id)
    second = documents.request_parse(db_session, ctx, document.id)

    chunks = list(
        db_session.scalars(select(DocumentChunk).where(DocumentChunk.document_id == document.id))
    )
    assert first.status == "completed"
    assert second.status == "completed"
    assert len(chunks) == 1
    assert chunks[0].organization_id == ctx.organization_id


def test_document_service_records_audit_events(
    db_session: Session,
    service_factories: ServiceFactories,
) -> None:
    factories = service_factories
    ctx = factories.ctx
    case = factories.cases.create()
    document = factories.documents.create_uploaded(case.id)
    documents.request_parse(db_session, ctx, document.id)

    event_types = set(db_session.scalars(select(AuditEvent.event_type)))
    assert {
        "document.upload_requested",
        "document.upload_completed",
        "document.parse_requested",
        "document.parsed",
    }.issubset(event_types)
