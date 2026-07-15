from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditEvent, DocumentChunk, StoredObject
from app.services import documents
from tests.services.factories import ServiceFactories, upload_payload


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
        storage_client=factories.storage,
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
