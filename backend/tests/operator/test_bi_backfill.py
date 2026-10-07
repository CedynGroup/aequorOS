"""Operator-triggered BI mart backfill: ``POST /operator/v1/tenants/{org}/bi/backfill``.

Same contract as every Tenant Inspector fix (``test_inspector_fix_api.py``):
an ACTIVE inspection session (403 ``inspection_required`` otherwise), a
required ``note``, the write on the operator's cross-tenant session, exactly
one ``bi.backfill`` audit row naming the tenant, and org-scoping (a sibling
tenant's bank is a 404). Plus the BI-specific refusals: a chain already
queued or running (409), no snapshot to start from (409), a window that runs
the wrong way (409), and the enqueue switch off (409).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Job, OperatorAuditLog
from app.services.bi.versions import BUILDER_VERSION
from tests.operator.conftest import operator_headers, provision_payload, start_inspection
from tests.support.factories.canonical import FIXTURE_AS_OF, seed_canonical_fixture

BASE = "/operator/v1/tenants"
UNTIL = date(2026, 1, 31)


@pytest.fixture
def bi_enqueue_on(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("BI_MART_ENQUEUE_ENABLED", "1")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _provision(client: TestClient, **overrides: object) -> tuple[str, str]:
    body = client.post(BASE, json=provision_payload(**overrides), headers=operator_headers()).json()
    assert body["succeeded"] is True, body
    return body["organization_id"], body["bank_id"]


def _audit_rows(db: Session) -> list[OperatorAuditLog]:
    return list(
        db.scalars(select(OperatorAuditLog).where(OperatorAuditLog.action == "bi.backfill"))
    )


def _backfill_jobs(db: Session, organization_id: str) -> list[Job]:
    db.expire_all()
    return list(
        db.scalars(
            select(Job)
            .where(Job.organization_id == organization_id, Job.job_type == "bi_mart_backfill")
            .order_by(Job.queued_at)
        )
    )


def _body(bank_id: str, **overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "bank_id": bank_id,
        "from_date": FIXTURE_AS_OF.isoformat(),
        "until_date": UNTIL.isoformat(),
        "note": "build the history for the pilot dashboards",
    }
    body.update(overrides)
    return body


# -- the gate --------------------------------------------------------------------


@pytest.mark.usefixtures("bi_enqueue_on")
def test_backfill_requires_an_active_inspection_session(
    operator_client: TestClient, operator_db: Session
) -> None:
    organization_id, bank_id = _provision(operator_client)
    response = operator_client.post(
        f"{BASE}/{organization_id}/bi/backfill", json=_body(bank_id), headers=operator_headers()
    )
    assert response.status_code == 403, response.text
    assert response.json()["error"]["details"]["code"] == "inspection_required"
    assert _backfill_jobs(operator_db, organization_id) == []
    assert _audit_rows(operator_db) == []


@pytest.mark.usefixtures("bi_enqueue_on")
def test_backfill_note_is_required(operator_client: TestClient) -> None:
    organization_id, bank_id = _provision(operator_client)
    start_inspection(operator_client, organization_id)
    response = operator_client.post(
        f"{BASE}/{organization_id}/bi/backfill",
        json=_body(bank_id, note=""),
        headers=operator_headers(),
    )
    assert response.status_code == 422, response.text


# -- happy path ------------------------------------------------------------------


@pytest.mark.usefixtures("bi_enqueue_on")
def test_backfill_enqueues_one_chain_and_audits_it(
    operator_client: TestClient, operator_db: Session
) -> None:
    organization_id, bank_id = _provision(operator_client)
    session_id = start_inspection(operator_client, organization_id)

    response = operator_client.post(
        f"{BASE}/{organization_id}/bi/backfill", json=_body(bank_id), headers=operator_headers()
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["job_type"] == "bi_mart_backfill"
    assert body["status"] == "queued"
    assert body["bank_id"] == bank_id
    assert body["cursor_date"] == FIXTURE_AS_OF.isoformat()
    assert body["until_date"] == UNTIL.isoformat()
    assert body["builder_version"] == BUILDER_VERSION

    (job,) = _backfill_jobs(operator_db, organization_id)
    assert str(job.id) == body["job_id"]
    assert job.bank_id == bank_id
    assert job.coalesce_key == f"bi-backfill:{bank_id}"
    assert job.entity_type == "bank"
    assert job.entity_id == bank_id
    assert job.run_after is not None  # runnable now, not debounced
    assert job.payload == {
        "organization_id": organization_id,
        "bank_id": bank_id,
        "cursor_date": FIXTURE_AS_OF.isoformat(),
        "until_date": UNTIL.isoformat(),
        "builder_version": BUILDER_VERSION,
        "reason": "build the history for the pilot dashboards",
        "initiated_by": "operator_bi_backfill",
    }

    (row,) = _audit_rows(operator_db)
    assert row.target_org == organization_id
    assert row.detail["session_id"] == session_id
    assert row.detail["note"] == "build the history for the pilot dashboards"
    assert row.detail["bank_id"] == bank_id
    assert row.detail["cursor_date"] == FIXTURE_AS_OF.isoformat()
    assert row.detail["until_date"] == UNTIL.isoformat()
    assert row.detail["builder_version"] == BUILDER_VERSION
    assert row.detail["job_id"] == body["job_id"]


@pytest.mark.usefixtures("bi_enqueue_on")
def test_backfill_defaults_from_date_to_the_latest_snapshot(
    operator_client: TestClient, operator_db: Session
) -> None:
    organization_id, bank_id = _provision(operator_client)
    seed_canonical_fixture(operator_db, organization_id=organization_id, bank_id=bank_id)
    operator_db.commit()
    start_inspection(operator_client, organization_id)

    response = operator_client.post(
        f"{BASE}/{organization_id}/bi/backfill",
        json=_body(bank_id, from_date=None),
        headers=operator_headers(),
    )
    assert response.status_code == 200, response.text
    assert response.json()["cursor_date"] == FIXTURE_AS_OF.isoformat()
    (job,) = _backfill_jobs(operator_db, organization_id)
    assert job.payload["cursor_date"] == FIXTURE_AS_OF.isoformat()


# -- refusals --------------------------------------------------------------------


@pytest.mark.usefixtures("bi_enqueue_on")
def test_backfill_without_a_snapshot_or_from_date_is_409(
    operator_client: TestClient, operator_db: Session
) -> None:
    organization_id, bank_id = _provision(operator_client)
    start_inspection(operator_client, organization_id)
    response = operator_client.post(
        f"{BASE}/{organization_id}/bi/backfill",
        json=_body(bank_id, from_date=None),
        headers=operator_headers(),
    )
    assert response.status_code == 409, response.text
    assert "no canonical position snapshot" in response.text
    assert _backfill_jobs(operator_db, organization_id) == []
    assert _audit_rows(operator_db) == []


@pytest.mark.usefixtures("bi_enqueue_on")
def test_backfill_window_must_run_newest_first(
    operator_client: TestClient, operator_db: Session
) -> None:
    organization_id, bank_id = _provision(operator_client)
    start_inspection(operator_client, organization_id)
    response = operator_client.post(
        f"{BASE}/{organization_id}/bi/backfill",
        json=_body(bank_id, from_date=UNTIL.isoformat(), until_date=FIXTURE_AS_OF.isoformat()),
        headers=operator_headers(),
    )
    assert response.status_code == 409, response.text
    assert "newest-first" in response.text
    assert _backfill_jobs(operator_db, organization_id) == []
    assert _audit_rows(operator_db) == []


@pytest.mark.usefixtures("bi_enqueue_on")
@pytest.mark.parametrize("live_status", ["queued", "running"])
def test_backfill_refuses_while_a_chain_is_already_live(
    operator_client: TestClient, operator_db: Session, live_status: str
) -> None:
    """A second chain would merge into the queued hop's payload and silently
    rewrite its cursor; a running one would build the same dates twice."""
    organization_id, bank_id = _provision(operator_client)
    start_inspection(operator_client, organization_id)
    first = operator_client.post(
        f"{BASE}/{organization_id}/bi/backfill", json=_body(bank_id), headers=operator_headers()
    )
    assert first.status_code == 200, first.text
    (job,) = _backfill_jobs(operator_db, organization_id)
    if live_status == "running":
        job.status = "running"
        operator_db.commit()

    second = operator_client.post(
        f"{BASE}/{organization_id}/bi/backfill",
        json=_body(bank_id, until_date="2025-12-31", note="again"),
        headers=operator_headers(),
    )
    assert second.status_code == 409, second.text
    assert f"already {live_status}" in second.text
    (job,) = _backfill_jobs(operator_db, organization_id)
    assert job.payload["until_date"] == UNTIL.isoformat()  # the first chain is untouched
    assert len(_audit_rows(operator_db)) == 1


@pytest.mark.usefixtures("bi_enqueue_on")
def test_backfill_may_start_again_once_the_previous_chain_finished(
    operator_client: TestClient, operator_db: Session
) -> None:
    organization_id, bank_id = _provision(operator_client)
    start_inspection(operator_client, organization_id)
    first = operator_client.post(
        f"{BASE}/{organization_id}/bi/backfill", json=_body(bank_id), headers=operator_headers()
    )
    assert first.status_code == 200, first.text
    (job,) = _backfill_jobs(operator_db, organization_id)
    job.status = "succeeded"
    operator_db.commit()

    second = operator_client.post(
        f"{BASE}/{organization_id}/bi/backfill", json=_body(bank_id), headers=operator_headers()
    )
    assert second.status_code == 200, second.text
    assert len(_backfill_jobs(operator_db, organization_id)) == 2
    assert len(_audit_rows(operator_db)) == 2


@pytest.mark.usefixtures("bi_enqueue_on")
def test_backfill_unknown_bank_is_404(operator_client: TestClient, operator_db: Session) -> None:
    organization_id, _bank = _provision(operator_client)
    start_inspection(operator_client, organization_id)
    response = operator_client.post(
        f"{BASE}/{organization_id}/bi/backfill",
        json=_body("BK-NOPE0001"),
        headers=operator_headers(),
    )
    assert response.status_code == 404, response.text
    assert _backfill_jobs(operator_db, organization_id) == []
    assert _audit_rows(operator_db) == []


@pytest.mark.usefixtures("bi_enqueue_on")
def test_backfill_foreign_bank_is_404(operator_client: TestClient, operator_db: Session) -> None:
    """A sibling tenant's bank does not exist from inside this tenant's session."""
    _org_a, bank_a = _provision(operator_client)
    org_b, _bank_b = _provision(
        operator_client,
        organization_name="Backfill Holdings",
        bank_name="Backfill Bank",
        admin_email="admin@backfill.example",
    )
    start_inspection(operator_client, org_b)
    response = operator_client.post(
        f"{BASE}/{org_b}/bi/backfill", json=_body(bank_a), headers=operator_headers()
    )
    assert response.status_code == 404, response.text
    assert _backfill_jobs(operator_db, org_b) == []
    assert _audit_rows(operator_db) == []


def test_backfill_is_refused_while_mart_builds_are_switched_off(
    operator_client: TestClient, operator_db: Session
) -> None:
    """BI_MART_ENQUEUE_ENABLED is pinned off in conftest: the worker would
    only mark every hop skipped, so the request is refused, not queued."""
    organization_id, bank_id = _provision(operator_client)
    start_inspection(operator_client, organization_id)
    response = operator_client.post(
        f"{BASE}/{organization_id}/bi/backfill", json=_body(bank_id), headers=operator_headers()
    )
    assert response.status_code == 409, response.text
    assert "BI_MART_ENQUEUE_ENABLED" in response.text
    assert _backfill_jobs(operator_db, organization_id) == []
    assert _audit_rows(operator_db) == []
