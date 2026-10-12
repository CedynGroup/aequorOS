"""API transactions survive supported cross-service waits on PostgreSQL."""

from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor
from typing import cast

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.bi.models.marts import BiQueryLog
from app.core.config import get_settings
from app.db.session import get_bi_engine
from app.models import AuditEvent, Document, StoredObject
from app.services import documents
from tests.bi.api.test_bi_exports import SUMMARY_QUERY
from tests.bi.api.test_bi_routes import AGGREGATE_ONLY, BASE, grant_only, seed_bi_mart
from tests.conftest import FakeStorage
from tests.services.factories import ServiceFactories
from tests.support.helpers import ORG_1, USER_1, headers

pytestmark = [
    pytest.mark.committing_db,
    pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="PostgreSQL required"),
]


def test_slow_bi_export_commits_provenance_audit_and_query_log(
    db_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_bi_mart(db_session)
    db_session.commit()
    authv = grant_only(db_session, AGGREGATE_ONLY)
    engine = cast(Engine, db_session.get_bind())
    monkeypatch.setenv("BI_ENABLED", "1")
    monkeypatch.setenv("BI_DATABASE_URL", engine.url.render_as_string(hide_password=False))
    get_settings.cache_clear()
    get_bi_engine.cache_clear()
    try:
        # Real database contention delays the normal BI statement on its own
        # pool; the API transaction waits while that statement runs.
        with (
            engine.connect() as blocker,
            engine.connect() as observer,
            ThreadPoolExecutor() as jobs,
        ):
            blocker.execute(text("LOCK TABLE bi_agg_position_daily IN ACCESS EXCLUSIVE MODE"))
            response_future = jobs.submit(
                db_client.post,
                f"{BASE}/export",
                json={"query": SUMMARY_QUERY, "format": "csv"},
                headers=headers(authorization_version=authv),
            )
            deadline = time.monotonic() + 15
            while not observer.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                    "WHERE application_name='aequoros-bi' AND wait_event_type='Lock')"
                )
            ):
                observer.rollback()
                assert not response_future.done(), "Export did not reach the separate BI pool"
                assert time.monotonic() < deadline, "BI statement did not wait on the mart lock"
                time.sleep(0.1)
            time.sleep(65)
            blocker.rollback()
            response = response_future.result(timeout=20)
        assert response.status_code == 200, response.text
        assert "B1,400.000000" in response.text
        assert "Exported by,demo.user.one@example.test" in response.text
        with Session(engine) as persisted:
            log = persisted.scalar(
                select(BiQueryLog).where(
                    BiQueryLog.organization_id == ORG_1, BiQueryLog.surface == "export"
                )
            )
            assert log is not None
            assert log.row_count == 2
            assert log.duration_ms is not None and log.duration_ms >= 65_000
            audit = persisted.scalar(
                select(AuditEvent).where(AuditEvent.event_type == "bi.export.delivered")
            )
            assert audit is not None
            assert audit.details["row_count"] == 2
            assert audit.details["build_fingerprint"]
    finally:
        get_bi_engine(engine.url.render_as_string(hide_password=False)).dispose()
        get_bi_engine.cache_clear()
        get_settings.cache_clear()


def test_slow_storage_deletion_commits_matching_document_and_object_states(
    db_session: Session,
) -> None:
    class SlowStorage(FakeStorage):
        def delete_object(self, *, bucket: str, object_key: str) -> None:
            time.sleep(65)
            super().delete_object(bucket=bucket, object_key=object_key)

    storage = SlowStorage()
    storage.deleted = []
    ctx = TenantContext(organization_id=ORG_1, actor_user_id=USER_1)
    factories = ServiceFactories(db_session, storage, get_settings(), ctx)
    document = factories.documents.create_uploaded()
    document_id, object_id = document.id, document.stored_object_id
    engine = cast(Engine, db_session.get_bind())
    documents.delete_document(db_session, ctx, document_id, storage_client=storage)
    with Session(engine) as persisted:
        deleted_document = persisted.get(Document, document_id)
        deleted_object = persisted.get(StoredObject, object_id)
        assert deleted_document is not None and deleted_object is not None
        assert deleted_document.status == deleted_object.status == "deleted"
        assert deleted_document.deleted_at is not None and deleted_object.deleted_at is not None
        assert storage.deleted == [(deleted_object.bucket, deleted_object.object_key)]
        assert (
            persisted.scalar(
                select(AuditEvent).where(
                    AuditEvent.event_type == "document.deleted",
                    AuditEvent.entity_id == str(document_id),
                )
            )
            is not None
        )
