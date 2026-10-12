"""Committed deletion survives storage waits beyond the API's idle budget.

Use migrated disposable schemas and real API/worker role rights. The accelerated
one-second budget keeps the regression fast; storage waits for three such budgets.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from http.client import HTTPResponse
from typing import cast
from urllib.request import Request, urlopen

import pytest
from sqlalchemy import Engine, create_engine, select, text
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.config import get_settings
from app.db.base import utc_now
from app.integrations.storage.s3 import S3ObjectStorage
from app.models import AuditEvent, Document, Job, StoredObject
from app.services import documents, job_queue
from tests.conftest import FakeStorage
from tests.db import test_database_roles as role_tests
from tests.services.factories import ServiceFactories
from tests.support.helpers import ORG_1

pytestmark = pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="PostgreSQL required")
role_schema = role_tests.role_schema
administrator = role_tests.administrator
role_engines = role_tests.role_engines


@pytest.fixture
def timeout_role_engines(role_engines: dict[str, Engine]) -> Iterator[dict[str, Engine]]:
    engines: dict[str, Engine] = {}
    for role in ("aqs_api", "aqs_worker"):
        url = role_engines[role].url
        options = " ".join(url.normalized_query.get("options", ()))
        engines[role] = create_engine(
            url,
            connect_args={"options": options + " -c idle_in_transaction_session_timeout=1000"},
        )
    try:
        yield engines
    finally:
        for engine in engines.values():
            engine.dispose()


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
                time.sleep(3)
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
