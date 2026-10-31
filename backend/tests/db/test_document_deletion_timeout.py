"""Committed deletion survives storage waits beyond the API's idle budget.

Use migrated disposable schemas and real API/worker role rights. The accelerated
one-second budget keeps the regression fast; dedicated storage probes exceed it.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from http.client import HTTPResponse
from threading import Event
from typing import cast
from urllib.request import Request, urlopen
from uuid import UUID

import pytest
from fastapi import HTTPException
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.case.service import documents
from app.core.config import Settings, get_settings
from app.db.base import utc_now
from app.integrations.storage.base import StoredObjectHead
from app.integrations.storage.s3 import S3ObjectStorage
from app.models import AuditEvent, Document, Job, StoredObject
from app.services import job_queue
from tests.case.factories import ServiceFactories
from tests.conftest import FakeStorage
from tests.db import test_database_roles as role_tests
from tests.support.database import managed_engine
from tests.support.helpers import ORG_1

pytestmark = pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="PostgreSQL required")
role_schema = role_tests.role_schema
administrator = role_tests.administrator
role_engines = role_tests.role_engines


@pytest.fixture
def timeout_role_engines(
    role_engines: dict[str, Engine], request: pytest.FixtureRequest
) -> Iterator[dict[str, Engine]]:
    engines: dict[str, Engine] = {}
    for role in ("aqs_api", "aqs_worker"):
        url = role_engines[role].url
        options = " ".join(url.normalized_query.get("options", ()))
        engines[role] = managed_engine(
            request,
            url,
            connect_args={"options": options + " -c idle_in_transaction_session_timeout=1000"},
        )
    try:
        yield engines
    finally:
        for engine in engines.values():
            engine.dispose()


class BlockingHeadStorage(FakeStorage):
    def __init__(self) -> None:
        self.entered_head = Event()
        self.resume_head = Event()
        self.head: StoredObjectHead | None = StoredObjectHead(
            content_type="application/pdf", byte_size=1234, etag='"etag"'
        )
        self.deleted = []

    def head_object(self, *, bucket: str, object_key: str) -> StoredObjectHead | None:
        captured_head = self.head
        self.entered_head.set()
        assert self.resume_head.wait(15)
        return captured_head


@dataclass
class UploadDeletionRace:
    api_engine: Engine
    worker_engine: Engine
    ctx: TenantContext
    settings: Settings
    storage: BlockingHeadStorage
    document_id: UUID
    object_id: UUID
    locator: tuple[str, str]
    expire_on_commit: bool
    upload_pid: int = 0

    def complete_upload(self) -> str:
        with self.api_engine.connect() as connection:
            self.upload_pid = cast(int, connection.scalar(text("SELECT pg_backend_pid()")))
            # PID discovery is test setup: do not spend the accelerated idle
            # budget before the service starts. Retain this exact backend for
            # the transaction-state assertion while storage is blocked.
            connection.commit()
            with Session(connection, expire_on_commit=self.expire_on_commit) as completing:
                completing.info["organization_id"] = ORG_1
                result = documents.complete_upload(
                    completing,
                    self.ctx,
                    self.document_id,
                    settings=self.settings,
                    storage_client=self.storage,
                )
                return result.status

    def cleanup(self) -> None:
        with Session(self.worker_engine, expire_on_commit=False) as worker:
            worker.info["organization_id"] = ORG_1
            job = worker.scalar(select(Job).where(Job.entity_id == str(self.document_id)))
            assert job is not None
            documents.run_document_delete(worker, job)
            job_queue.complete(worker, job)


def build_upload_deletion_race(
    timeout_role_engines: dict[str, Engine],
    setup_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    expire_on_commit: bool,
) -> UploadDeletionRace:
    api_engine = timeout_role_engines["aqs_api"]
    ctx = TenantContext(organization_id=ORG_1)
    settings = get_settings()
    storage = BlockingHeadStorage()
    monkeypatch.setattr(documents, "get_object_storage", lambda: storage)
    # Fixture preparation is outside the accelerated idle-budget scenario.
    # Keep real API authority without timing synthetic case construction.
    with Session(setup_engine, expire_on_commit=False) as setup:
        setup.info["organization_id"] = ORG_1
        upload = ServiceFactories(setup, storage, settings, ctx).documents.request_upload()
        document = documents.get_document_or_404(setup, ORG_1, upload.document_id)
        stored = documents.get_stored_object_or_404(setup, ORG_1, document.stored_object_id)
        object_id, locator = stored.id, (stored.bucket, stored.object_key)

    return UploadDeletionRace(
        api_engine=api_engine,
        worker_engine=timeout_role_engines["aqs_worker"],
        ctx=ctx,
        settings=settings,
        storage=storage,
        document_id=upload.document_id,
        object_id=object_id,
        locator=locator,
        expire_on_commit=expire_on_commit,
    )


def delete_during_upload_head(
    scenario: UploadDeletionRace, administrator: Engine, cleanup_during_head: bool
) -> None:
    with ThreadPoolExecutor(max_workers=1) as executor:
        completing = executor.submit(scenario.complete_upload)
        try:
            assert scenario.storage.entered_head.wait(10)
            with administrator.connect() as observer:
                assert (
                    observer.scalar(
                        text("SELECT xact_start FROM pg_stat_activity WHERE pid=:pid"),
                        {"pid": scenario.upload_pid},
                    )
                    is None
                )
            with Session(scenario.api_engine, expire_on_commit=False) as deleting:
                deleting.info["organization_id"] = ORG_1
                deleted = documents.delete_document(deleting, scenario.ctx, scenario.document_id)
                assert deleted.status == "deleted"
            if cleanup_during_head:
                scenario.cleanup()
                assert scenario.storage.deleted == [scenario.locator]
            scenario.storage.resume_head.set()
            with pytest.raises(HTTPException) as refused:
                completing.result(timeout=10)
            assert refused.value.status_code == 409
            if not cleanup_during_head:
                scenario.cleanup()
        finally:
            scenario.storage.resume_head.set()


def assert_upload_deletion_outcome(scenario: UploadDeletionRace) -> None:
    with Session(scenario.api_engine, expire_on_commit=False) as observer:
        observer.info["organization_id"] = ORG_1
        deleted = documents.get_document_or_404(observer, ORG_1, scenario.document_id)
        removed = documents.get_stored_object_or_404(observer, ORG_1, scenario.object_id)
        assert deleted.status == "deleted" and deleted.deleted_at is not None
        assert removed.status == "deleted" and removed.deleted_at is not None
        events = list(
            observer.scalars(
                select(AuditEvent.event_type).where(
                    AuditEvent.entity_id == str(scenario.document_id)
                )
            )
        )
        assert events.count("document.deleted") == 1
        assert "document.upload_completed" not in events
        jobs = list(observer.scalars(select(Job).where(Job.entity_id == str(scenario.document_id))))
        assert len(jobs) == 1 and jobs[0].status == "succeeded"
        with pytest.raises(HTTPException) as unavailable:
            documents.create_download_url(
                observer,
                scenario.ctx,
                scenario.document_id,
                settings=scenario.settings,
                storage_client=scenario.storage,
            )
        assert unavailable.value.status_code == 409
    assert scenario.storage.deleted == [scenario.locator]


@pytest.mark.parametrize("cleanup_during_head", [False, True])
@pytest.mark.parametrize("expire_on_commit", [False, True])
def test_upload_completion_cannot_resurrect_deletion_during_head(  # noqa: PLR0913
    administrator: Engine,
    timeout_role_engines: dict[str, Engine],
    role_engines: dict[str, Engine],
    monkeypatch: pytest.MonkeyPatch,
    cleanup_during_head: bool,
    expire_on_commit: bool,
) -> None:
    scenario = build_upload_deletion_race(
        timeout_role_engines, role_engines["aqs_api"], monkeypatch, expire_on_commit
    )

    delete_during_upload_head(scenario, administrator, cleanup_during_head)

    assert_upload_deletion_outcome(scenario)


def test_upload_completion_succeeds_after_head_exceeds_idle_budget(
    timeout_role_engines: dict[str, Engine],
) -> None:
    with Session(timeout_role_engines["aqs_api"], expire_on_commit=False) as api:
        api.info["organization_id"] = ORG_1
        ctx = TenantContext(organization_id=ORG_1)
        settings = get_settings()

        class SlowStorage(FakeStorage):
            def head_object(self, *, bucket: str, object_key: str) -> StoredObjectHead | None:
                assert not api.in_transaction()
                time.sleep(1.5)
                return StoredObjectHead(
                    content_type="application/pdf", byte_size=1234, etag='"etag"'
                )

        storage = SlowStorage()
        upload = ServiceFactories(api, storage, settings, ctx).documents.request_upload()
        uploaded = documents.complete_upload(
            api, ctx, upload.document_id, settings=settings, storage_client=storage
        )
        assert uploaded.status == "uploaded" and uploaded.uploaded_at is not None
        stored = documents.get_stored_object_or_404(api, ORG_1, uploaded.stored_object_id)
        assert stored.status == "available" and stored.etag == '"etag"'
        assert (
            api.scalar(
                select(AuditEvent.id).where(
                    AuditEvent.entity_id == str(upload.document_id),
                    AuditEvent.event_type == "document.upload_completed",
                )
            )
            is not None
        )
        assert (
            documents.create_download_url(
                api, ctx, upload.document_id, settings=settings, storage_client=storage
            ).url
            == storage.download_url
        )


def test_repeated_deletion_refreshes_preloaded_state(
    role_engines: dict[str, Engine],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = FakeStorage()
    monkeypatch.setattr(documents, "get_object_storage", lambda: storage)
    with (
        Session(role_engines["aqs_api"], expire_on_commit=False) as first,
        Session(role_engines["aqs_api"], expire_on_commit=False) as repeated,
    ):
        first.info["organization_id"] = repeated.info["organization_id"] = ORG_1
        ctx = TenantContext(organization_id=ORG_1)
        upload = ServiceFactories(first, storage, get_settings(), ctx).documents.request_upload()
        stale = documents.get_document_or_404(repeated, ORG_1, upload.document_id)
        repeated.commit()
        deleted = documents.delete_document(first, ctx, upload.document_id)
        tombstone = deleted.deleted_at
        assert stale.deleted_at is None
        again = documents.delete_document(repeated, ctx, upload.document_id)
        assert again.deleted_at == tombstone and again.status == "deleted"
        assert (
            len(
                list(
                    repeated.scalars(select(Job.id).where(Job.entity_id == str(upload.document_id)))
                )
            )
            == 1
        )
        assert (
            len(
                list(
                    repeated.scalars(
                        select(AuditEvent.id).where(
                            AuditEvent.entity_id == str(upload.document_id),
                            AuditEvent.event_type == "document.deleted",
                        )
                    )
                )
            )
            == 1
        )
        repeated.commit()
    with Session(role_engines["aqs_worker"], expire_on_commit=False) as worker:
        worker.info["organization_id"] = ORG_1
        job = worker.scalar(select(Job).where(Job.entity_id == str(upload.document_id)))
        assert job is not None
        documents.run_document_delete(worker, job)
        job_queue.complete(worker, job)


def test_deletion_commits_before_storage_and_retries_under_role_timeouts(
    administrator: Engine,
    timeout_role_engines: dict[str, Engine],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api_engine, worker_engine = timeout_role_engines["aqs_api"], timeout_role_engines["aqs_worker"]
    with Session(api_engine, expire_on_commit=False) as api:
        api.info["organization_id"] = ORG_1
        ctx = TenantContext(organization_id=ORG_1)
        assert api.scalar(text("SHOW idle_in_transaction_session_timeout")) == "1s"
        api.commit()
        factories = ServiceFactories(api, FakeStorage(), get_settings(), ctx)
        document = factories.documents.create_uploaded()
        document_id, object_id = document.id, document.stored_object_id
        documents.delete_document(api, ctx, document_id)
        assert not api.in_transaction()
        with Session(administrator) as observer:
            pending = observer.get(StoredObject, object_id)
            deleted = observer.get(Document, document_id)
            job = observer.scalar(select(Job).where(Job.entity_id == str(document_id)))
            assert pending is not None and pending.status == "pending_deletion"
            assert deleted is not None and deleted.status == "deleted"
            assert job is not None and job.status == "queued"
            assert (
                observer.scalar(
                    select(AuditEvent.id).where(
                        AuditEvent.entity_id == str(document_id),
                        AuditEvent.event_type == "document.deleted",
                    )
                )
                is not None
            )

    with Session(worker_engine, expire_on_commit=False) as worker:
        worker.info["organization_id"] = ORG_1
        job = job_queue.claim_next(worker, utc_now(), ("document_delete",))
        assert job is not None
        pid = cast(int, worker.scalar(text("SELECT pg_backend_pid()")))
        calls = 0

        class SlowStorage(FakeStorage):
            def delete_object(self, *, bucket: str, object_key: str) -> None:
                nonlocal calls
                calls += 1
                assert not worker.in_transaction()
                with administrator.connect() as observer:
                    assert (
                        observer.scalar(
                            text("SELECT xact_start FROM pg_stat_activity WHERE pid=:pid"),
                            {"pid": pid},
                        )
                        is None
                    )
                time.sleep(1.5)
                if calls == 1:
                    raise TimeoutError("ambiguous storage failure")
                super().delete_object(bucket=bucket, object_key=object_key)

        monkeypatch.setattr(documents, "get_object_storage", SlowStorage)
        with pytest.raises(TimeoutError):
            documents.run_document_delete(worker, job)
        worker.rollback()
        stored = worker.get(StoredObject, object_id)
        assert stored is not None and stored.status == "pending_deletion"
        documents.run_document_delete(worker, job)
        job_queue.complete(worker, job)
        assert stored.status == "deleted" and job.status == "succeeded"
        documents.run_document_delete(worker, job)
        job_queue.complete(worker, job)
        assert calls == 2


@pytest.mark.skipif(not os.getenv("S3_ENDPOINT"), reason="local MinIO required")
def test_cleanup_deletes_real_minio_object_and_reruns_safely(
    role_engines: dict[str, Engine], monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = get_settings()
    storage = S3ObjectStorage(
        region_name=settings.risk_s3_region,
        endpoint_url=settings.risk_s3_endpoint_url,
        access_key_id=settings.risk_s3_access_key_id,
        secret_access_key=settings.risk_s3_secret_access_key,
        force_path_style=True,
    )
    with Session(role_engines["aqs_api"], expire_on_commit=False) as api:
        api.info["organization_id"] = ORG_1
        ctx = TenantContext(organization_id=ORG_1)
        factories = ServiceFactories(api, FakeStorage(), settings, ctx)
        document = factories.documents.create_uploaded()
        stored = api.get(StoredObject, document.stored_object_id)
        assert stored is not None
        bucket, key, document_id = stored.bucket, stored.object_key, document.id
        api.commit()
        upload = storage.create_presigned_upload_url(
            bucket=bucket, object_key=key, content_type="application/pdf", expires_seconds=60
        )
        try:
            with cast(
                HTTPResponse,
                urlopen(
                    Request(
                        upload.url, data=b"synthetic document", headers=upload.headers, method="PUT"
                    ),
                    timeout=10,
                ),
            ) as response:
                assert response.status == 200
            assert storage.head_object(bucket=bucket, object_key=key) is not None
            documents.delete_document(api, ctx, document_id)
            monkeypatch.setattr(documents, "get_object_storage", lambda: storage)
            with Session(role_engines["aqs_worker"], expire_on_commit=False) as worker:
                worker.info["organization_id"] = ORG_1
                job = job_queue.claim_next(worker, utc_now(), ("document_delete",))
                assert job is not None
                documents.run_document_delete(worker, job)
                job_queue.complete(worker, job)
                assert storage.head_object(bucket=bucket, object_key=key) is None
                documents.run_document_delete(worker, job)
                job_queue.complete(worker, job)
                assert job.status == "succeeded"
        finally:
            storage.delete_object(bucket=bucket, object_key=key)
