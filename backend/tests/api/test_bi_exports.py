"""``POST /banks/{id}/bi/export`` and its collection route: the policy, end to end.

The artifacts themselves are ``tests/services/bi/test_exports.py``. What this
file asserts is the thing that decides whether a file is produced at all, and
for whom:

* **a summary export needs ``view``; a record-level or confidential one needs
  ``export`` as well.** A ``viewer`` who can drill into position references
  interactively is refused the SAME query as an export, with no bytes.
* **an export can never return a member the caller could not query
  interactively.** The elevated permission does not stand in for the ordinary
  one: the decision is the read surface's own ``authorize_query``, asked twice.
* **one ``bi_query_log`` row per request**, with ``surface="export"``.
* **the async threshold moves the work to the ``bi`` lane** rather than
  answering inline, and the finished file is collectable only by the principal
  who asked for it.

The mart is the read surface's own fixture (``tests/api/test_bi_routes``) so the
two surfaces cannot be tested against different data. The flag and cross-tenant
sweeps are there too, parametrised over ``ROUTES``, which now includes this one.
"""

from __future__ import annotations

import datetime as dt
import io
from collections.abc import Iterable, Iterator
from typing import Any, Literal, cast
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.authorization import ModuleScope, RoleBundle, SensitivityScope
from app.core.config import get_settings
from app.features import export_bi
from app.jobs import bi_export as bi_export_job
from app.models import AuditEvent, Bank, Job, User
from app.models.bi import BiQueryLog
from app.services import job_queue
from app.services.bi.exports import jobs as export_jobs
from app.services.bi.exports import policy
from app.storage.client import (
    ObjectMetadata,
    StorageClient,
    StorageHealth,
    StorageLocation,
    StorageObject,
)
from tests.api.test_bi_routes import (
    AGGREGATE_ONLY,
    AS_OF,
    BANK_ID,
    BASE,
    Grant,
    grant_only,
    seed_bi_mart,
)
from tests.support.helpers import ORG_1, USER_1, headers

SUMMARY_QUERY: dict[str, Any] = {
    "measures": ["loans.balance_rc"],
    "dimensions": ["branch.code"],
    "time": {"as_of": AS_OF.isoformat()},
}
#: A position reference identifies ONE loan, so this is the record-level class.
RECORD_QUERY: dict[str, Any] = {
    "measures": ["loans.balance_rc"],
    "dimensions": ["position.source_reference"],
    "time": {"as_of": AS_OF.isoformat()},
}
#: A counterparty NAME names a legal person: restricted, and therefore also the
#: record-level class (D-066).
OBLIGOR_QUERY: dict[str, Any] = {
    "measures": ["loans.balance_rc"],
    "dimensions": ["counterparty.name"],
    "time": {"as_of": AS_OF.isoformat()},
}

#: ``view`` only, over both pairs a record-level query needs. Enough to DRILL,
#: deliberately not enough to export the same rows.
RECORD_VIEW_ONLY: tuple[Grant, ...] = (
    Grant(ModuleScope.CREDIT),
    Grant(ModuleScope.RISK),
    Grant(ModuleScope.RISK, SensitivityScope.CONFIDENTIAL),
)
#: The same scopes carried by the ``analyst`` bundle, which holds ``export``.
RECORD_EXPORTER: tuple[Grant, ...] = tuple(
    Grant(grant.module, grant.sensitivity, grant.institution, RoleBundle.ANALYST)
    for grant in RECORD_VIEW_ONLY
)
#: ``export`` over the aggregated pairs ONLY — nothing covering an obligor name.
AGGREGATE_EXPORTER: tuple[Grant, ...] = tuple(
    Grant(grant.module, grant.sensitivity, grant.institution, RoleBundle.ANALYST)
    for grant in AGGREGATE_ONLY
)


@pytest.fixture
def mart(db_session: Session) -> Bank:
    bank = seed_bi_mart(db_session)
    # The async path writes to the institution's bucket, which is keyed by the
    # storage slug the Data Engine assigns on first ingestion. BI must never
    # write one itself (it may write ``bi_*`` tables and nothing else), so the
    # fixture stands in for the ingestion that would have.
    bank.storage_slug = "bi-export-test"
    db_session.commit()
    return bank


@pytest.fixture
def bi_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BI_ENABLED", "1")
    get_settings.cache_clear()


@pytest.fixture
def user_email(db_session: Session) -> str:
    user = db_session.get(User, USER_1)
    assert user is not None
    return user.email


def export(client: TestClient, body: dict[str, Any], *, authv: int, fmt: str = "csv") -> Any:
    return client.post(
        f"{BASE}/export",
        json={"query": body, "format": fmt},
        headers=headers(authorization_version=authv),
    )


def log_rows(db: Session) -> list[BiQueryLog]:
    return list(
        db.scalars(
            select(BiQueryLog)
            .where(BiQueryLog.organization_id == ORG_1, BiQueryLog.surface == "export")
            .order_by(BiQueryLog.queried_at)
        )
    )


# --- the policy --------------------------------------------------------------------------


def test_a_viewer_gets_a_summary_export(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None, user_email: str
) -> None:
    """``view`` is the whole sentence a summary export needs."""

    authv = grant_only(db_session, AGGREGATE_ONLY)
    response = export(db_client, SUMMARY_QUERY, authv=authv)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    assert response.headers["x-bi-export-class"] == policy.SUMMARY
    body = response.text
    # ``branch.code`` is the code, not the branch name: the export carries the
    # compiler's own columns and invents no display join.
    assert "B1,400.000000" in body and "B2,200.000000" in body
    assert f"Exported by,{user_email}" in body


def test_a_view_only_principal_is_refused_a_record_level_export(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    """The rule, and the whole reason this surface exists separately.

    The SAME principal may drill into these rows interactively — which is
    asserted here rather than assumed, because a 403 that merely restates a read
    denial would prove nothing about the export sentence.
    """

    authv = grant_only(db_session, RECORD_VIEW_ONLY)
    drill = db_client.post(
        f"{BASE}/drill",
        json={"query": RECORD_QUERY},
        headers=headers(authorization_version=authv),
    )
    assert drill.status_code == 200, drill.text
    assert "L-1" in drill.text

    refused = export(db_client, RECORD_QUERY, authv=authv)
    assert refused.status_code == 403, refused.text
    details = refused.json()["error"]["details"]
    assert details["error_code"] == "bi_authorization_denied"
    assert set(details["denied_members"]) == {"loans.balance_rc", "position.source_reference"}
    # No bytes, no reference, nothing.
    assert "L-1" not in refused.text


def test_an_exporter_gets_the_record_level_file(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    authv = grant_only(db_session, RECORD_EXPORTER)
    response = export(db_client, RECORD_QUERY, authv=authv)
    assert response.status_code == 200, response.text
    assert response.headers["x-bi-export-class"] == policy.RECORD_LEVEL
    assert "L-1" in response.text
    assert "Disclosure class,Record level" in response.text


def test_an_export_of_a_query_the_caller_cannot_run_is_refused(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    """``export`` does not stand in for ``view`` on a member nobody granted.

    The principal holds the ``analyst`` bundle — which carries ``export`` — over
    the aggregated pairs and nothing else. An obligor NAME is CREDIT/restricted,
    so the interactive query is refused and so is the export, with the same
    member named.
    """

    authv = grant_only(db_session, AGGREGATE_EXPORTER)
    interactive = db_client.post(
        f"{BASE}/query", json=OBLIGOR_QUERY, headers=headers(authorization_version=authv)
    )
    assert interactive.status_code == 403, interactive.text

    refused = export(db_client, OBLIGOR_QUERY, authv=authv)
    assert refused.status_code == 403, refused.text
    assert "counterparty.name" in refused.json()["error"]["details"]["denied_members"]
    assert "Ada Traders" not in refused.text


def test_the_class_is_not_taken_from_the_client(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    """The request body has no class field at all; sending one is a 422."""

    authv = grant_only(db_session, RECORD_EXPORTER)
    response = db_client.post(
        f"{BASE}/export",
        json={"query": SUMMARY_QUERY, "format": "csv", "export_class": "summary"},
        headers=headers(authorization_version=authv),
    )
    assert response.status_code == 422, response.text


@pytest.mark.parametrize("fmt", ["csv", "xlsx", "pdf"])
def test_every_format_is_served_with_its_own_media_type(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None, fmt: str
) -> None:
    authv = grant_only(db_session, AGGREGATE_ONLY)
    response = export(db_client, SUMMARY_QUERY, authv=authv, fmt=fmt)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith(
        {"csv": "text/csv", "xlsx": "application/vnd.openxml", "pdf": "application/pdf"}[fmt]
    )
    assert response.content


def test_an_unknown_format_is_refused_before_anything_runs(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    authv = grant_only(db_session, AGGREGATE_ONLY)
    response = export(db_client, SUMMARY_QUERY, authv=authv, fmt="docx")
    assert response.status_code == 422, response.text
    assert log_rows(db_session) == []


# --- the log and the audit trail -----------------------------------------------------------


def test_an_export_writes_exactly_one_query_log_row(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    authv = grant_only(db_session, AGGREGATE_ONLY)
    assert log_rows(db_session) == []
    response = export(db_client, SUMMARY_QUERY, authv=authv)
    assert response.status_code == 200, response.text
    rows = log_rows(db_session)
    assert len(rows) == 1
    row = rows[0]
    assert row.decision == "allowed"
    assert row.bank_id == BANK_ID
    assert row.principal_user_id == USER_1
    assert row.row_count == 2
    assert "loans.balance_rc" in row.member_ids
    # The log is value-free: the query is a hash, never the filter it carried.
    assert len(row.query_hash) == 64


def test_a_refused_export_is_logged_as_a_denial_and_serves_nothing(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    authv = grant_only(db_session, RECORD_VIEW_ONLY)
    assert export(db_client, RECORD_QUERY, authv=authv).status_code == 403
    rows = log_rows(db_session)
    assert len(rows) == 1
    assert rows[0].decision == "denied"
    assert rows[0].row_count is None
    assert set(rows[0].denied_members) == {"loans.balance_rc", "position.source_reference"}


def test_an_export_is_audited(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    """``docs/bi.md``: exports also go to ``audit_events``."""

    authv = grant_only(db_session, RECORD_EXPORTER)
    assert export(db_client, RECORD_QUERY, authv=authv).status_code == 200
    event = db_session.scalars(
        select(AuditEvent).where(AuditEvent.event_type == export_bi.EVENT_DELIVERED)
    ).one()
    assert event.organization_id == ORG_1
    assert event.actor_user_id == USER_1
    assert event.details["bank_id"] == BANK_ID
    assert event.details["export_class"] == policy.RECORD_LEVEL
    assert event.details["delivery"] == "inline"
    # Four positions carry a source reference: three loans and the deposit.
    assert event.details["row_count"] == 4


# --- the asynchronous path -------------------------------------------------------------------


class _FakeStorage(StorageClient):
    """An in-memory object store: the async path's only external dependency."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.metadata: dict[str, ObjectMetadata] = {}

    def write(
        self,
        location: StorageLocation,
        data: Any,
        metadata: ObjectMetadata,
        content_type: str = "application/octet-stream",
    ) -> StorageObject:
        payload = data.read()
        self.objects[location.object_path] = payload
        self.metadata[location.object_path] = metadata
        return StorageObject(
            location=location,
            metadata=metadata,
            size_bytes=len(payload),
            version_id=None,
            created_at=dt.datetime.now(dt.UTC),
            content_type=content_type,
        )

    def read(self, location: StorageLocation, version_id: str | None = None) -> Any:
        return None, io.BytesIO(self.objects[location.object_path])

    def exists(self, location: StorageLocation) -> bool:
        return location.object_path in self.objects

    def list(self, *args: Any, **kwargs: Any) -> Iterator[StorageObject]:
        return iter(())

    def list_versions(self, location: StorageLocation) -> Iterator[StorageObject]:
        return iter(())

    def delete(self, location: StorageLocation) -> None:
        self.objects.pop(location.object_path, None)

    def get_metadata(
        self, location: StorageLocation, version_id: str | None = None
    ) -> ObjectMetadata:
        return self.metadata[location.object_path]

    def presigned_url(
        self,
        location: StorageLocation,
        operation: Literal["read", "write"],
        expires_in_seconds: int = 900,
    ) -> str:
        del operation, expires_in_seconds
        return f"https://storage.test/{location.bucket_name('test')}/{location.object_path}"

    def health_check(self) -> StorageHealth:
        return StorageHealth(healthy=True, backend="fake")


@pytest.fixture
def storage(db_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> Iterator[_FakeStorage]:
    fake = _FakeStorage()
    db_client.app.dependency_overrides[export_bi.storage_client] = lambda: fake  # type: ignore[union-attr]
    monkeypatch.setattr(bi_export_job, "storage_client", lambda: fake)
    yield fake
    db_client.app.dependency_overrides.pop(export_bi.storage_client, None)  # type: ignore[union-attr]


@pytest.fixture
def tiny_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every non-trivial export an asynchronous one.

    The product threshold is 10 000 rows and is asserted separately; lowering it
    here exercises the BRANCH without seeding ten thousand mart rows, which is
    what the setting exists for.
    """

    monkeypatch.setenv("BI_EXPORT_ASYNC_THRESHOLD_ROWS", "1")
    get_settings.cache_clear()


def test_the_product_threshold_is_the_specs_ten_thousand_rows() -> None:
    settings = get_settings().bi
    assert settings.export_async_threshold_rows == 10_000
    assert settings.export_row_cap == 100_000
    assert settings.export_timeout_ms == 120_000


def test_over_the_threshold_the_export_is_queued_not_answered(  # noqa: PLR0913 - one fixture per guard in the chain
    db_client: TestClient,
    db_session: Session,
    mart: Bank,
    bi_on: None,
    tiny_threshold: None,
) -> None:
    authv = grant_only(db_session, AGGREGATE_ONLY)
    response = export(db_client, SUMMARY_QUERY, authv=authv)
    assert response.status_code == 202, response.text
    payload = response.json()
    assert payload["state"] == "queued"
    assert payload["download_url"] is None
    # No rows in the body: the answer is a promise, not a partial file.
    assert "Head office" not in response.text

    job = db_session.scalars(
        select(Job).where(Job.organization_id == ORG_1, Job.job_type == export_jobs.JOB_TYPE)
    ).one()
    assert str(job.id) == payload["job_id"]
    assert job.bank_id == BANK_ID
    assert job.payload["principal_user_id"] == str(USER_1)
    assert job.payload["export_class"] == policy.SUMMARY

    rows = log_rows(db_session)
    assert len(rows) == 1
    # Nothing was served by this request; the render writes its own row.
    assert rows[0].row_count is None
    event = db_session.scalars(
        select(AuditEvent).where(AuditEvent.event_type == export_bi.EVENT_QUEUED)
    ).one()
    assert event.details["delivery"] == "asynchronous"


def test_the_queued_job_belongs_to_the_bi_lane_and_has_a_handler() -> None:
    """D-008: a job type with no handler in its lane sits ``queued`` for ever."""

    from app.worker import HANDLERS  # noqa: PLC0415 - the registration under test

    assert export_jobs.JOB_TYPE in job_queue.JOB_TYPES
    assert job_queue.lane_of(export_jobs.JOB_TYPE) == "bi"
    assert export_jobs.JOB_TYPE in HANDLERS
    assert export_jobs.JOB_TYPE in job_queue.job_types_in_lane("bi")
    # The reclaim window must exceed the longest legitimate render.
    assert job_queue.stale_after_for(
        export_jobs.JOB_TYPE, dt.timedelta(seconds=900)
    ) > dt.timedelta(seconds=900)


def test_the_handler_renders_stores_and_logs_the_queued_export(  # noqa: PLR0913 - one fixture per guard in the chain
    db_client: TestClient,
    db_session: Session,
    mart: Bank,
    bi_on: None,
    tiny_threshold: None,
    storage: _FakeStorage,
) -> None:
    authv = grant_only(db_session, AGGREGATE_ONLY)
    assert export(db_client, SUMMARY_QUERY, authv=authv).status_code == 202
    job = db_session.scalars(select(Job).where(Job.job_type == export_jobs.JOB_TYPE)).one()

    bi_export_job.run_bi_export(db_session, job)
    db_session.commit()

    assert job.progress["status"] == export_jobs.STATUS_SUCCEEDED
    assert job.progress["row_count"] == 2
    stored = storage.objects[job.progress["object_path"]]
    assert b"B1,400.000000" in stored
    assert job.progress["size_bytes"] == len(stored)
    # Two rows now: the request that queued it, and the render that produced it.
    rows = log_rows(db_session)
    assert len(rows) == 2
    assert rows[-1].decision == "allowed"
    assert rows[-1].row_count == 2
    assert db_session.scalars(
        select(AuditEvent).where(AuditEvent.event_type == bi_export_job.EVENT_COMPLETED)
    ).one()


def test_the_handler_refuses_an_export_whose_authority_was_revoked(  # noqa: PLR0913 - one fixture per guard in the chain
    db_client: TestClient,
    db_session: Session,
    mart: Bank,
    bi_on: None,
    tiny_threshold: None,
    storage: _FakeStorage,
) -> None:
    """Minutes pass between the enqueue and the render; a queued row is not a session."""

    authv = grant_only(db_session, AGGREGATE_ONLY)
    assert export(db_client, SUMMARY_QUERY, authv=authv).status_code == 202
    job = db_session.scalars(select(Job).where(Job.job_type == export_jobs.JOB_TYPE)).one()

    grant_only(db_session, ())  # every binding revoked

    bi_export_job.run_bi_export(db_session, job)
    db_session.commit()

    assert job.progress["status"] == export_jobs.STATUS_DENIED
    assert job.progress["reason"] == "authorization_revoked"
    assert storage.objects == {}
    assert db_session.scalars(
        select(AuditEvent).where(AuditEvent.event_type == bi_export_job.EVENT_DENIED)
    ).one()


def test_the_handler_does_nothing_when_the_deployment_flag_is_off(  # noqa: PLR0913 - one fixture per guard in the chain
    db_client: TestClient,
    db_session: Session,
    mart: Bank,
    bi_on: None,
    tiny_threshold: None,
    storage: _FakeStorage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The kill-switch idiom: a queued job must not produce a file after the flag."""

    authv = grant_only(db_session, AGGREGATE_ONLY)
    assert export(db_client, SUMMARY_QUERY, authv=authv).status_code == 202
    job = db_session.scalars(select(Job).where(Job.job_type == export_jobs.JOB_TYPE)).one()

    monkeypatch.setenv("BI_ENABLED", "0")
    get_settings.cache_clear()
    bi_export_job.run_bi_export(db_session, job)

    assert job.progress == {
        "status": "skipped",
        "reason": bi_export_job.SKIP_REASON_DISABLED,
    }
    assert storage.objects == {}


# --- collecting a finished export -------------------------------------------------------------


def _finished_job(client: TestClient, db: Session, *, authv: int) -> Job:
    """Queue an export and run it the way ``worker.run_once`` would.

    The handler sets ``jobs.progress``; the QUEUE sets ``jobs.status``. The
    collection route reads both, so a test that skipped ``complete`` would be
    polling a job the worker never finished.
    """

    assert export(client, SUMMARY_QUERY, authv=authv).status_code == 202
    job = db.scalars(select(Job).where(Job.job_type == export_jobs.JOB_TYPE)).one()
    bi_export_job.run_bi_export(db, job)
    job_queue.complete(db, job, job.progress)
    return job


def test_the_owner_collects_a_short_lived_link(  # noqa: PLR0913 - one fixture per guard in the chain
    db_client: TestClient,
    db_session: Session,
    mart: Bank,
    bi_on: None,
    tiny_threshold: None,
    storage: _FakeStorage,
) -> None:
    authv = grant_only(db_session, AGGREGATE_ONLY)
    job = _finished_job(db_client, db_session, authv=authv)

    response = db_client.get(
        f"{BASE}/exports/{job.id}", headers=headers(authorization_version=authv)
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["state"] == "ready"
    assert payload["download_url"].startswith("https://storage.test/")
    assert payload["download_expires_in_seconds"] == export_jobs.DOWNLOAD_EXPIRY_SECONDS
    assert payload["row_count"] == 2
    assert payload["checksum_sha256"]
    assert payload["message"] == export_bi.STATE_MESSAGES["ready"]


def test_a_queued_export_reports_its_state_without_a_link(  # noqa: PLR0913 - one fixture per guard in the chain
    db_client: TestClient,
    db_session: Session,
    mart: Bank,
    bi_on: None,
    tiny_threshold: None,
    storage: _FakeStorage,
) -> None:
    authv = grant_only(db_session, AGGREGATE_ONLY)
    assert export(db_client, SUMMARY_QUERY, authv=authv).status_code == 202
    job = db_session.scalars(select(Job).where(Job.job_type == export_jobs.JOB_TYPE)).one()

    response = db_client.get(
        f"{BASE}/exports/{job.id}", headers=headers(authorization_version=authv)
    )
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "queued"
    assert response.json()["download_url"] is None


def test_another_principal_cannot_collect_someone_elses_export(  # noqa: PLR0913 - one fixture per guard in the chain
    db_client: TestClient,
    db_session: Session,
    mart: Bank,
    bi_on: None,
    tiny_threshold: None,
    storage: _FakeStorage,
) -> None:
    """A presigned GET carries no identity, so the owner check IS the control."""

    authv = grant_only(db_session, AGGREGATE_ONLY)
    job = _finished_job(db_client, db_session, authv=authv)
    job.payload = {**job.payload, "principal_user_id": str(uuid4())}
    db_session.commit()

    response = db_client.get(
        f"{BASE}/exports/{job.id}", headers=headers(authorization_version=authv)
    )
    assert response.status_code == 404, response.text
    assert "storage.test" not in response.text


def test_an_export_that_does_not_exist_is_404(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None, storage: _FakeStorage
) -> None:
    authv = grant_only(db_session, AGGREGATE_ONLY)
    response = db_client.get(
        f"{BASE}/exports/{UUID(int=7)}", headers=headers(authorization_version=authv)
    )
    assert response.status_code == 404, response.text


def test_a_browser_can_read_the_export_headers_it_needs() -> None:
    """CORS exposes a short safelist by default, so these must be named.

    The export route sets the filename and the disclosure class as response
    headers, and without `expose_headers` a browser cannot READ either: the
    dashboard silently fell back to a filename it composed itself while the
    server's choice never arrived, and it could not tell a summary export from a
    record-level one. Measured on a real download while the export menu was being
    wired.

    Asserted two ways, because either alone is weak. The declared list must name
    the headers the export route actually sets, read out of that route's own
    source rather than restated here; and the middleware must be given that list,
    so declaring it and not passing it fails too.
    """
    import inspect as _inspect  # noqa: PLC0415

    from fastapi.middleware.cors import CORSMiddleware  # noqa: PLC0415

    from app.core.logging import REQUEST_ID_HEADER  # noqa: PLC0415
    from app.features import export_bi  # noqa: PLC0415
    from app.main import EXPOSED_RESPONSE_HEADERS, create_app  # noqa: PLC0415

    route_source = _inspect.getsource(export_bi)
    for header in ("Content-Disposition", "X-Bi-Export-Class"):
        assert header in route_source, (
            f"{header} is no longer set by the export route, so exposing it is stale"
        )
        assert header in EXPOSED_RESPONSE_HEADERS, (
            f"the export route sets {header} and a browser cannot read it"
        )
    assert REQUEST_ID_HEADER in EXPOSED_RESPONSE_HEADERS

    # And the middleware must actually be GIVEN the list. The test environment
    # blanks the allowed origins, so the layer is only added when a deployment
    # configures one; where it is added, it must carry these.
    app = create_app()
    for middleware in app.user_middleware:
        if middleware.cls is CORSMiddleware:
            declared = middleware.kwargs.get("expose_headers") or ()
            exposed = set(cast("Iterable[str]", declared))
            assert set(EXPOSED_RESPONSE_HEADERS) <= exposed, sorted(exposed)
