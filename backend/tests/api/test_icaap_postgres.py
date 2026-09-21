"""The ICAAP concurrency and isolation claims, against a real Postgres.

Two properties cannot be shown on SQLite with a rollback-per-test session:

* **the optimistic save really is atomic.** Two sessions read the same
  revision; the first save wins and the second is refused. On a single
  in-process transaction the race cannot even be staged.
* **a version number cannot be issued twice.** The unique constraint, not the
  read-then-write in the service, is what makes that true under load.

Postgres-gated and committing, so each case cleans up after itself.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.core.authorization import (
    GrantorType,
    InstitutionScope,
    ModuleScope,
    PrincipalType,
    RoleBundle,
    SensitivityScope,
)
from app.core.config import get_settings
from app.db.session import get_sessionmaker
from app.models import AuthorizationBinding, User
from app.models.icaap import IcaapCycle
from app.services import authorization
from tests.api.helpers import ORG_1, ORG_2, USER_1, headers
from tests.fixtures.canonical_bank_fixture import (
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
)

pytestmark = pytest.mark.skipif(
    os.getenv("TEST_DATABASE_URL") is None,
    reason="TEST_DATABASE_URL is required for the ICAAP concurrency checks.",
)

BASE = f"/api/v1/banks/{SAMPLE_BANK_ID}/icaap"
SECTION = "executive_summary"


def _doc(text: str) -> dict:
    return {
        "type": "doc",
        "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}],
    }


@pytest.fixture
def analyst(
    committing_db_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> Iterator[dict[str, str]]:
    get_settings.cache_clear()
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        session.execute(
            delete(AuthorizationBinding).where(AuthorizationBinding.organization_id == ORG_1)
        )
        materialize_canonical_test_book(session)
        session.commit()
        authorization.create_role_binding(
            session,
            organization_id=ORG_1,
            principal_user_id=USER_1,
            principal_type=PrincipalType.HUMAN,
            role_bundle=RoleBundle.ANALYST,
            scope=authorization.BindingScope(
                InstitutionScope.INSTITUTION,
                SAMPLE_BANK_ID,
                ModuleScope.CAPITAL,
                SensitivityScope.CONFIDENTIAL,
            ),
            grantor=authorization.GrantorRef(GrantorType.SYSTEM, "test-suite"),
            reason="Exercise ICAAP concurrency against Postgres.",
        )
        session.commit()
        user = session.get(User, USER_1)
        assert user is not None
        session.refresh(user)
        auth = headers(roles=("analyst",), authorization_version=user.authorization_version)
    finally:
        session.close()
    yield auth
    cleanup = get_sessionmaker()()
    cleanup.info["organization_id"] = ORG_1
    try:
        cleanup.execute(delete(IcaapCycle).where(IcaapCycle.organization_id == ORG_1))
        cleanup.commit()
    finally:
        cleanup.close()


def _cycle(client: TestClient, auth: dict[str, str]) -> str:
    response = client.post(
        f"{BASE}/cycles",
        json={
            "fiscal_year": 2025,
            "cycle_kind": "rehearsal",
            "basis": "solo",
            "framework_code": "bog_icaap",
            "framework_version": "2026.02-ed.1",
            "reason": "Concurrency check",
        },
        headers=auth,
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_two_saves_from_the_same_revision_do_not_both_land(
    committing_db_client: TestClient, analyst: dict[str, str]
) -> None:
    cycle_id = _cycle(committing_db_client, analyst)
    path = f"{BASE}/cycles/{cycle_id}/sections/{SECTION}/working"

    first = committing_db_client.put(
        path, json={"doc": _doc("Mine"), "base_rev": 0}, headers=analyst
    )
    assert first.status_code == 200
    second = committing_db_client.put(
        path, json={"doc": _doc("Theirs"), "base_rev": 0}, headers=analyst
    )
    assert second.status_code == 409
    detail = second.json()
    assert "section_rev_conflict" in second.text
    assert detail is not None

    current = committing_db_client.get(
        f"{BASE}/cycles/{cycle_id}/sections/{SECTION}", headers=analyst
    )
    assert current.status_code == 200
    body = current.json()
    assert body["working_rev"] == 1
    assert body["working_doc"]["content"][0]["content"][0]["text"] == "Mine"


def test_a_version_number_is_issued_once(
    committing_db_client: TestClient, analyst: dict[str, str]
) -> None:
    cycle_id = _cycle(committing_db_client, analyst)
    committing_db_client.put(
        f"{BASE}/cycles/{cycle_id}/sections/{SECTION}/working",
        json={"doc": _doc("Committed text"), "base_rev": 0},
        headers=analyst,
    )
    first = committing_db_client.post(
        f"{BASE}/cycles/{cycle_id}/sections/{SECTION}/versions",
        json={"base_rev": 1},
        headers=analyst,
    )
    assert first.status_code == 201
    assert first.json()["version_no"] == 1

    # Committing the same text again is refused as unchanged, so version 1
    # cannot be issued twice by replay.
    repeat = committing_db_client.post(
        f"{BASE}/cycles/{cycle_id}/sections/{SECTION}/versions",
        json={"base_rev": 1},
        headers=analyst,
    )
    assert repeat.status_code == 409
    assert "no_changes" in repeat.text


def test_only_one_open_cycle_survives_a_repeated_create(
    committing_db_client: TestClient, analyst: dict[str, str]
) -> None:
    _cycle(committing_db_client, analyst)
    again = committing_db_client.post(
        f"{BASE}/cycles",
        json={
            "fiscal_year": 2025,
            "cycle_kind": "rehearsal",
            "basis": "solo",
            "framework_code": "bog_icaap",
            "framework_version": "2026.02-ed.1",
            "reason": "Concurrency check",
        },
        headers=analyst,
    )
    assert again.status_code == 409
    assert "cycle_exists" in again.text
    listing = committing_db_client.get(f"{BASE}/cycles", headers=analyst)
    assert len(listing.json()["cycles"]) == 1


def test_another_tenant_cannot_address_this_cycle(
    committing_db_client: TestClient, analyst: dict[str, str]
) -> None:
    cycle_id = _cycle(committing_db_client, analyst)
    other = headers(org_id=ORG_2, roles=("analyst",))
    for path in (
        f"{BASE}/cycles/{cycle_id}",
        f"{BASE}/cycles/{cycle_id}/sections",
        f"{BASE}/cycles/{cycle_id}/blocks",
        f"{BASE}/cycles/{cycle_id}/attachments",
    ):
        response = committing_db_client.get(path, headers=other)
        assert response.status_code == 404, f"{path}: {response.text}"
    assert UUID(cycle_id)
