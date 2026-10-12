"""Deletion outbox: committed tombstone -> storage cleanup -> acknowledgement.

Repeated delete preserves the tombstone and job. Transport failure or a lost
acknowledgement preserves pending cleanup; retry is safe. Other tenants cannot
request deletion. Upload completion, parsing and downloads refuse tombstones.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi import HTTPException
from hypothesis import settings
from hypothesis.stateful import (
    RuleBasedStateMachine,
    invariant,
    rule,
    run_state_machine_as_test,  # pyright: ignore[reportUnknownVariableType] - Hypothesis factory generic
)
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.db.base import utc_now
from app.models import AuditEvent, Job, StoredObject
from app.services import documents, job_queue
from app.worker import run_once
from tests.services.factories import ServiceFactories
from tests.support.helpers import ORG_2


@pytest.mark.committing_db
def test_storage_failure_leaves_committed_pending_cleanup_that_worker_retries(
    db_session: Session, service_factories: ServiceFactories, monkeypatch: pytest.MonkeyPatch
) -> None:
    factories = service_factories
    document = factories.documents.create_uploaded()
    deleted = documents.delete_document(db_session, factories.ctx, document.id)
    assert deleted.status == "deleted"
    stored = db_session.get(StoredObject, document.stored_object_id)
    assert stored is not None and stored.status == "pending_deletion"
    db_session.commit()
    unavailable = True

    def delete_object(*, bucket: str, object_key: str) -> None:
        # An independent connection sees the committed tombstone before storage
        # can destroy anything, even on an ambiguous transport failure.
        with Session(db_session.get_bind()) as observer:
            pending = observer.get(StoredObject, document.stored_object_id)
            assert pending is not None and pending.status == "pending_deletion"
        if unavailable:
            raise TimeoutError("storage unavailable")

    monkeypatch.setattr(factories.storage, "delete_object", delete_object)
    monkeypatch.setattr(documents, "get_object_storage", lambda: factories.storage)
    assert run_once(("document_delete",))
    db_session.expire_all()
    job = db_session.scalar(select(Job).where(Job.job_type == "document_delete"))
    assert job is not None and job.status == "queued" and job.attempts == 1
    assert stored.status == "pending_deletion" and document.status == "deleted"
    job.run_after = utc_now() - timedelta(seconds=1)
    db_session.commit()
    unavailable = False
    assert run_once(("document_delete",))
    db_session.expire_all()
    assert stored.status == "deleted" and job.status == "succeeded"
    assert not run_once(("document_delete",))


def test_database_failure_before_outbox_commit_never_calls_storage(
    db_session: Session, service_factories: ServiceFactories, monkeypatch: pytest.MonkeyPatch
) -> None:
    factories = service_factories
    document = factories.documents.create_uploaded()
    document_id, object_id = document.id, document.stored_object_id

    def forbidden_storage(*, bucket: str, object_key: str) -> None:
        pytest.fail("storage ran before deletion committed")

    def fail_commit(_session: Session) -> None:
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(factories.storage, "delete_object", forbidden_storage)
    monkeypatch.setattr(documents, "get_object_storage", lambda: factories.storage)
    event.listen(db_session, "before_commit", fail_commit, once=True)
    with pytest.raises(RuntimeError, match="database unavailable"):
        documents.delete_document(db_session, factories.ctx, document_id)
    db_session.rollback()
    survivor = documents.get_document_or_404(db_session, factories.ctx.organization_id, document_id)
    assert survivor.status == "uploaded"
    stored = db_session.get(StoredObject, object_id)
    assert stored is not None and stored.status == "available"
    assert db_session.scalar(select(Job).where(Job.job_type == "document_delete")) is None


def test_storage_success_with_lost_acknowledgement_is_safe_to_repeat(
    db_session: Session, service_factories: ServiceFactories, monkeypatch: pytest.MonkeyPatch
) -> None:
    factories = service_factories
    document = factories.documents.create_uploaded()
    documents.delete_document(db_session, factories.ctx, document.id)
    job = job_queue.claim_next(db_session, utc_now(), ("document_delete",))
    assert job is not None
    deleted_keys: set[tuple[str, str]] = set()

    def delete_object(*, bucket: str, object_key: str) -> None:
        assert not db_session.in_transaction()
        deleted_keys.add((bucket, object_key))

    monkeypatch.setattr(factories.storage, "delete_object", delete_object)
    monkeypatch.setattr(documents, "get_object_storage", lambda: factories.storage)
    documents.run_document_delete(db_session, job)
    # Storage succeeded, but the acknowledgement transaction was lost.
    db_session.rollback()
    stored = db_session.get(StoredObject, document.stored_object_id)
    assert stored is not None and stored.status == "pending_deletion"
    documents.run_document_delete(db_session, job)
    job_queue.complete(db_session, job)
    assert stored.status == "deleted" and job.status == "succeeded"
    assert len(deleted_keys) == 1
    documents.run_document_delete(db_session, job)
    job_queue.complete(db_session, job)
    assert stored.status == "deleted"


def test_deletion_state_machine(
    db_session: Session, service_factories: ServiceFactories, monkeypatch: pytest.MonkeyPatch
) -> None:
    factories = service_factories
    monkeypatch.setattr(documents, "get_object_storage", lambda: factories.storage)

    class DeletionMachine(RuleBasedStateMachine):
        def __init__(self) -> None:
            super().__init__()
            self.document = factories.documents.create_uploaded()
            self.state = "live"

        @rule()
        def delete(self) -> None:
            documents.delete_document(db_session, factories.ctx, self.document.id)
            if self.state == "live":
                self.state = "pending"

        @rule()
        def cleanup(self) -> None:
            job = db_session.scalar(
                select(Job).where(
                    Job.entity_id == str(self.document.id), Job.job_type == "document_delete"
                )
            )
            if job is not None:
                documents.run_document_delete(db_session, job)
                job_queue.complete(db_session, job)
                self.state = "deleted"

        @rule()
        def other_tenant_delete(self) -> None:
            with pytest.raises(HTTPException) as refused:
                documents.delete_document(
                    db_session, TenantContext(organization_id=ORG_2), self.document.id
                )
            assert refused.value.status_code == 404

        @invariant()
        def matches_model(self) -> None:
            stored = db_session.get(StoredObject, self.document.stored_object_id)
            assert stored is not None
            assert (
                stored.status
                == {"live": "available", "pending": "pending_deletion", "deleted": "deleted"}[
                    self.state
                ]
            )
            assert self.document.status == ("uploaded" if self.state == "live" else "deleted")
            jobs = list(
                db_session.scalars(
                    select(Job).where(
                        Job.entity_id == str(self.document.id), Job.job_type == "document_delete"
                    )
                )
            )
            assert len(jobs) == (0 if self.state == "live" else 1)
            audits = list(
                db_session.scalars(
                    select(AuditEvent).where(
                        AuditEvent.entity_id == str(self.document.id),
                        AuditEvent.event_type == "document.deleted",
                    )
                )
            )
            assert len(audits) == len(jobs)
            if self.state != "live":
                for command in (
                    lambda: documents.request_parse(db_session, factories.ctx, self.document.id),
                    lambda: documents.complete_upload(
                        db_session,
                        factories.ctx,
                        self.document.id,
                        settings=factories.settings,
                        storage_client=factories.storage,
                    ),
                    lambda: documents.create_download_url(
                        db_session,
                        factories.ctx,
                        self.document.id,
                        settings=factories.settings,
                        storage_client=factories.storage,
                    ),
                ):
                    with pytest.raises(HTTPException) as refused:
                        command()
                    assert refused.value.status_code == 409

    run_state_machine_as_test(
        DeletionMachine,
        settings=settings(max_examples=25, stateful_step_count=20, derandomize=True, deadline=None),
    )
