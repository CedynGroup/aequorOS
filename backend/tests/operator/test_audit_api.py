"""GET /operator/v1/audit — the operator's OWN cross-tenant action log read.

Distinct from ``test_audit_log.py`` (which pins WHAT gets logged + the
append-only trigger); this pins the READ endpoint: filters, paging, ordering,
and the operator_admin gate.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.base import utc_now
from app.models import OperatorAuditLog
from app.operator.services import operator_views
from app.schemas.operator import OperatorAuditLogListRead
from tests.operator.conftest import operator_headers, provision_payload
from tests.operator.test_operator_auth import PASSWORD, bearer, login, make_operator

AUDIT = "/operator/v1/audit"


def _seed_actions(client: TestClient) -> str:
    """Provision a tenant (→ tenants.provision) and list twice (→ tenants.list),
    returning the organization id."""
    provision = client.post(
        "/operator/v1/tenants", json=provision_payload(), headers=operator_headers()
    )
    organization_id = provision.json()["organization_id"]
    client.get("/operator/v1/tenants", headers=operator_headers())
    client.get("/operator/v1/tenants", headers=operator_headers())
    return organization_id


def test_requires_authentication(operator_client: TestClient) -> None:
    assert operator_client.get(AUDIT).status_code == 401


def test_developer_role_is_forbidden(operator_client: TestClient, operator_db: Session) -> None:
    make_operator(operator_db)  # developer
    headers = bearer(login(operator_client, "ama@aequoros.com", PASSWORD))
    assert operator_client.get(AUDIT, headers=headers).status_code == 403


def test_returns_rows_newest_first_with_total(
    operator_client: TestClient,
) -> None:
    _seed_actions(operator_client)
    response = operator_client.get(AUDIT, headers=operator_headers())
    assert response.status_code == 200
    body = response.json()
    actions = [item["action"] for item in body["items"]]
    assert actions == ["tenants.list", "tenants.list", "tenants.provision"]
    assert body["total"] == 3
    created = [item["created_at"] for item in body["items"]]
    assert created == sorted(created, reverse=True)
    # Shape check: OperatorAuditLogRead fields present.
    first = body["items"][0]
    assert set(first) == {
        "id",
        "operator_email",
        "auth_mode",
        "action",
        "target_org",
        "detail",
        "created_at",
    }


def test_action_filter_is_a_prefix_match(operator_client: TestClient) -> None:
    _seed_actions(operator_client)
    exact = operator_client.get(
        f"{AUDIT}?action=tenants.provision", headers=operator_headers()
    ).json()
    assert [i["action"] for i in exact["items"]] == ["tenants.provision"]
    assert exact["total"] == 1

    prefix = operator_client.get(f"{AUDIT}?action=tenants.", headers=operator_headers()).json()
    assert prefix["total"] == 3
    assert {i["action"] for i in prefix["items"]} == {"tenants.list", "tenants.provision"}


def test_target_org_filter(operator_client: TestClient) -> None:
    organization_id = _seed_actions(operator_client)
    # Every bank returned by the directory gets its own tenant-scoped evidence.
    scoped = operator_client.get(
        f"{AUDIT}?target_org={organization_id}", headers=operator_headers()
    ).json()
    assert [i["action"] for i in scoped["items"]] == [
        "tenants.list",
        "tenants.list",
        "tenants.provision",
    ]
    assert scoped["total"] == 3


def test_limit_and_offset_page_without_changing_total(
    operator_client: TestClient, operator_db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_actions(operator_client)
    operator_client.get(AUDIT, headers=operator_headers())
    server_now = utc_now()
    browser_now = server_now + timedelta(minutes=2)
    monkeypatch.setattr(operator_views, "utc_now", lambda: server_now)
    response = operator_client.get(
        AUDIT, params={"as_of": browser_now.isoformat(), "limit": 2}, headers=operator_headers()
    )
    assert response.status_code == 200, response.text
    page1 = OperatorAuditLogListRead.model_validate_json(response.text)
    assert page1.as_of == server_now and page1.as_of < browser_now
    assert len(page1.items) == 2 and page1.total == 4
    params: dict[str, str | int] = {"as_of": page1.as_of.isoformat(), "limit": 2, "offset": 0}
    response = operator_client.get(
        AUDIT, params={**params, "offset": 2}, headers=operator_headers()
    )
    assert response.status_code == 200, response.text
    page2 = OperatorAuditLogListRead.model_validate_json(response.text)
    assert len(page2.items) == 2 and page2.total == 4 and page2.as_of == page1.as_of
    assert {item.id for item in page1.items}.isdisjoint({item.id for item in page2.items})
    assert "audit.read" in {item.action for item in page1.items + page2.items}
    response = operator_client.get(AUDIT, params=params, headers=operator_headers())
    assert response.status_code == 200, response.text
    previous = OperatorAuditLogListRead.model_validate_json(response.text)
    assert previous == page1
    response = operator_client.get(AUDIT, params={**params, "limit": 3}, headers=operator_headers())
    assert response.status_code == 200, response.text
    resized = OperatorAuditLogListRead.model_validate_json(response.text)
    assert resized.total == 4 and resized.as_of == page1.as_of
    assert resized.items == (page1.items + page2.items)[:3]
    assert (
        operator_db.scalar(
            select(func.count())
            .select_from(OperatorAuditLog)
            .where(OperatorAuditLog.action == "audit.read")
        )
        == 5
    )
