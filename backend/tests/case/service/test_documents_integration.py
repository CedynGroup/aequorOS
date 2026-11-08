"""Tests for test_documents."""

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
from app.models import AuditEvent, Document, DocumentChunk, Job, StoredObject
from tests.case.factories import ServiceFactories, upload_payload
from tests.conftest import FakeStorage


def test_the_direct_parse_job_write_persists_locator_and_provenance(
    db_session: Session,
    service_factories: ServiceFactories,
) -> None:
    """The document service's direct Job(...) write stamps the locator itself (mission D9)."""
    factories = service_factories
    ctx = factories.ctx
    case = factories.cases.create()
    document = factories.documents.create_uploaded(case.id)

    result = documents.request_parse(db_session, ctx, document.id)

    job = db_session.get(Job, result.job_id)
    assert job is not None
    # The case workspace is organization-scoped: no bank dimension.
    assert job.institution_scope == "organization"
    assert job.bank_id is None
    assert job.module == "risk"
    assert job.sensitivity == "confidential"
    assert job.enqueued_by_principal == f"human:{ctx.actor_user_id}"
    assert job.enqueued_action == "document.parse_requested"


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
    assert "/finalized/" in stored_object.object_key


def test_complete_upload_binds_downloads_to_the_verified_snapshot(
    db_session: Session,
    service_factories: ServiceFactories,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factories = service_factories
    body = b"verified bytes"
    document_id, _ = _staged_upload(
        factories, declared=body, declared_sha256=hashlib.sha256(body).hexdigest()
    )
    digest_read = factories.storage.read_object_digest

    def replay_after_read(
        *, bucket: str, object_key: str, max_bytes: int
    ) -> tuple[int, str] | None:
        observed = digest_read(bucket=bucket, object_key=object_key, max_bytes=max_bytes)
        factories.storage.body = b"replayed bytes"
        factories.storage.head = StoredObjectHead(
            byte_size=14, etag='"replayed"', version_id="replayed"
        )
        return observed

    monkeypatch.setattr(factories.storage, "read_object_digest", replay_after_read)
    document = documents.complete_upload(
        db_session,
        factories.ctx,
        document_id,
        settings=factories.settings,
        storage_client=factories.storage,
    )
    stored = db_session.get(StoredObject, document.stored_object_id)
    assert stored is not None
    locator = (stored.bucket, stored.object_key)
    assert factories.storage.finalized_objects[locator][1] == body
    assert factories.storage.digest_reads == [locator]
    assert stored.etag == '"etag"'
    assert stored.version_id is None
    assert stored.sha256 == hashlib.sha256(body).hexdigest()

    documents.create_download_url(
        db_session,
        factories.ctx,
        document_id,
        settings=factories.settings,
        storage_client=factories.storage,
    )
    assert factories.storage.downloads == [locator]


@pytest.mark.parametrize("over_cap", [False, True])
def test_complete_upload_checks_snapshot_metadata_after_a_staged_overwrite(
    db_session: Session,
    service_factories: ServiceFactories,
    monkeypatch: pytest.MonkeyPatch,
    over_cap: bool,
) -> None:
    factories = service_factories
    body = b"declared"
    document_id, _ = _staged_upload(
        factories, declared=body, declared_sha256=hashlib.sha256(body).hexdigest()
    )
    finalize = factories.storage.finalize_upload

    def overwrite_before_copy(*, bucket: str, object_key: str) -> str | None:
        factories.storage.body = b"larger replacement"
        size = factories.settings.risk_max_upload_bytes + 1 if over_cap else 18
        factories.storage.head = StoredObjectHead(byte_size=size, etag='"replacement"')
        return finalize(bucket=bucket, object_key=object_key)

    monkeypatch.setattr(factories.storage, "finalize_upload", overwrite_before_copy)
    refused = _complete(factories, db_session, document_id)

    assert refused.status_code == 400
    assert ("maximum size" if over_cap else "declared size") in str(refused.detail)
    if over_cap:
        assert factories.storage.digest_reads == []
    assert not factories.storage.finalized_objects
    _assert_still_pending(db_session, document_id)


@pytest.mark.parametrize("operation", ["head", "digest"])
@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_complete_upload_cleans_up_after_storage_verification_errors(
    db_session: Session,
    service_factories: ServiceFactories,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    cleanup_fails: bool,
) -> None:
    factories = service_factories
    body = b"body"
    document_id, _ = _staged_upload(
        factories, declared=body, declared_sha256=hashlib.sha256(body).hexdigest()
    )
    head = factories.storage.head_object
    delete = factories.storage.delete_object
    original_error = RuntimeError("verification failed")
    attempted: list[str] = []

    def failing_head(*, bucket: str, object_key: str) -> StoredObjectHead | None:
        if "/finalized/" in object_key:
            raise original_error
        return head(bucket=bucket, object_key=object_key)

    def failing_digest(*, bucket: str, object_key: str, max_bytes: int) -> tuple[int, str] | None:
        raise original_error

    def cleanup(*, bucket: str, object_key: str) -> None:
        assert not db_session.in_transaction()
        attempted.append(object_key)
        if cleanup_fails:
            raise RuntimeError("cleanup failed")
        delete(bucket=bucket, object_key=object_key)

    monkeypatch.setattr(factories.storage, "delete_object", cleanup)
    if operation == "head":
        monkeypatch.setattr(factories.storage, "head_object", failing_head)
    else:
        monkeypatch.setattr(factories.storage, "read_object_digest", failing_digest)
    with pytest.raises(RuntimeError) as refused:
        documents.complete_upload(
            db_session,
            factories.ctx,
            document_id,
            settings=factories.settings,
            storage_client=factories.storage,
        )
    assert refused.value is original_error
    assert len(attempted) == 1 and "/finalized/" in attempted[0]
    assert bool(factories.storage.finalized_objects) == cleanup_fails
    _assert_still_pending(db_session, document_id)


@pytest.mark.parametrize(
    "outcome",
    ["committed", "failed", "lookup_failed", "rollback_failed", "cleanup_failed", "stale_lookup"],
)
def test_complete_upload_handles_uncertain_commit_without_losing_referenced_content(
    db_session: Session,
    service_factories: ServiceFactories,
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
) -> None:
    factories = service_factories
    document_id, _ = _staged_upload(factories, declared=b"body")
    commit = db_session.commit
    delete = factories.storage.delete_object
    commits = 0
    original_error = RuntimeError("commit acknowledgement lost")
    attempted: list[str] = []
    lookups: list[str] = []

    def failed_database(*args: object, **kwargs: object) -> None:
        raise RuntimeError("database unavailable")

    def stale_lookup(*args: object, **kwargs: object) -> str:
        lookups.append("scalar")
        return "stale-staging-key"

    def uncertain_commit() -> None:
        nonlocal commits
        commits += 1
        if commits == 1:
            commit()
            return
        if outcome == "committed":
            commit()
        elif outcome == "lookup_failed":
            patches.setattr(db_session, "scalar", failed_database)
        elif outcome == "rollback_failed":
            patches.setattr(db_session, "rollback", failed_database)
        elif outcome == "stale_lookup":
            patches.setattr(db_session, "scalar", stale_lookup)
        raise original_error

    def cleanup(*, bucket: str, object_key: str) -> None:
        assert not db_session.in_transaction()
        attempted.append(object_key)
        if outcome == "cleanup_failed":
            raise RuntimeError("cleanup unavailable")
        delete(bucket=bucket, object_key=object_key)

    with monkeypatch.context() as patches:
        patches.setattr(db_session, "commit", uncertain_commit)
        patches.setattr(factories.storage, "delete_object", cleanup)
        with pytest.raises(RuntimeError) as refused:
            documents.complete_upload(
                db_session,
                factories.ctx,
                document_id,
                settings=factories.settings,
                storage_client=factories.storage,
            )
    assert refused.value is original_error
    db_session.rollback()
    document = db_session.get(Document, document_id)
    assert document is not None
    stored = db_session.get(StoredObject, document.stored_object_id)
    assert stored is not None
    if outcome == "committed":
        assert document.status == "uploaded" and stored.status == "available"
        assert (stored.bucket, stored.object_key) in factories.storage.finalized_objects
    else:
        _assert_still_pending(db_session, document_id)
    assert not attempted and not lookups
    assert factories.storage.finalized_objects


@pytest.mark.parametrize("concurrent_action", ["complete", "delete"])
def test_complete_upload_discards_its_snapshot_when_the_pending_state_changes(
    db_session: Session,
    service_factories: ServiceFactories,
    monkeypatch: pytest.MonkeyPatch,
    concurrent_action: str,
) -> None:
    factories = service_factories
    body = b"verified body"
    document_id, _ = _staged_upload(
        factories, declared=body, declared_sha256=hashlib.sha256(body).hexdigest()
    )
    digest_read = factories.storage.read_object_digest

    def change_pending_state(
        *, bucket: str, object_key: str, max_bytes: int
    ) -> tuple[int, str] | None:
        observed = digest_read(bucket=bucket, object_key=object_key, max_bytes=max_bytes)
        monkeypatch.setattr(factories.storage, "read_object_digest", digest_read)
        if concurrent_action == "delete":
            documents.delete_document(db_session, factories.ctx, document_id)
        else:
            documents.complete_upload(
                db_session,
                factories.ctx,
                document_id,
                settings=factories.settings,
                storage_client=factories.storage,
            )
        return observed

    monkeypatch.setattr(factories.storage, "read_object_digest", change_pending_state)
    refused = _complete(factories, db_session, document_id)

    assert refused.status_code == 409
    document = db_session.get(Document, document_id)
    assert document is not None
    stored = db_session.get(StoredObject, document.stored_object_id)
    assert stored is not None
    if concurrent_action == "delete":
        assert document.status == "deleted"
        assert not factories.storage.finalized_objects
    else:
        assert document.status == "uploaded"
        assert list(factories.storage.finalized_objects) == [(stored.bucket, stored.object_key)]


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
    assert not factories.storage.finalized_objects


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

    document = db_session.get(Document, undeclared_id)
    assert document is not None
    stored = db_session.get(StoredObject, document.stored_object_id)
    assert stored is not None
    assert (stored.bucket, stored.object_key) in factories.storage.finalized_objects

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
    fake_storage: FakeStorage,
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

    assert refused.status_code == 400
    assert len(factories.storage.digest_reads) == 1
    # The refused read also removed the snapshot it would have hashed.
    assert len(fake_storage.deleted) == 1
    assert "/finalized/" in fake_storage.deleted[0][1]
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
