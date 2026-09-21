"""One preparer's pass through the ICAAP workspace, over HTTP."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.api.deps import TenantContext
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
from app.models import AuthorizationBinding, BankReportingPeriod, User
from app.schemas.regulatory_liquidity import RegulatoryRunCreate
from app.services import authorization, regulatory_capital
from tests.api.helpers import ORG_1, USER_1, headers
from tests.fixtures.canonical_bank_fixture import (
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
)

BASE = f"/api/v1/banks/{SAMPLE_BANK_ID}/icaap"
SECTION = "executive_summary"
AS_OF = "2025-12-31"


@pytest.fixture(autouse=True)
def _enable_workspace(monkeypatch: pytest.MonkeyPatch) -> None:
    get_settings.cache_clear()


@pytest.fixture
def auth(db_client: TestClient) -> dict[str, str]:
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
            reason="Exercise the ICAAP workspace over HTTP.",
        )
        period = session.scalar(
            select(BankReportingPeriod.id).where(
                BankReportingPeriod.bank_id == SAMPLE_BANK_ID,
                BankReportingPeriod.period_end == AS_OF,
            )
        )
        assert period is not None
        regulatory_capital.create_capital_run(
            session,
            _context(),
            SAMPLE_BANK_ID,
            RegulatoryRunCreate(
                module="capital", reporting_period_id=period, scenario_code="baseline"
            ),
        )
        session.commit()
        user = session.get(User, USER_1)
        assert user is not None
        session.refresh(user)
        return headers(roles=("analyst",), authorization_version=user.authorization_version)
    finally:
        session.close()


def _context():

    return TenantContext(organization_id=ORG_1, actor_user_id=USER_1, authorization_version=1)


def _create_cycle(client: TestClient, auth: dict[str, str]) -> dict[str, Any]:
    response = client.post(
        f"{BASE}/cycles",
        json={
            "fiscal_year": 2025,
            "cycle_kind": "rehearsal",
            "basis": "solo",
            "framework_code": "bog_icaap",
            "framework_version": "2026.02-ed.1",
            "reason": "Dry run before the first filing",
        },
        headers=auth,
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_the_framework_list_and_detail_are_readable(
    db_client: TestClient, auth: dict[str, str]
) -> None:
    listing = db_client.get(f"{BASE}/frameworks", headers=auth)
    assert listing.status_code == 200
    frameworks = listing.json()["frameworks"]
    assert frameworks and frameworks[0]["status"] == "exposure_draft"

    detail = db_client.get(f"{BASE}/frameworks/bog_icaap/versions/2026.02-ed.1", headers=auth)
    assert detail.status_code == 200
    body = detail.json()
    assert len(body["sections"]) == 17
    assert body["deadline"]["months_param_code"] == "icaap_submission_months"


def test_a_preparer_can_write_commit_link_a_figure_and_see_readiness(
    db_client: TestClient, auth: dict[str, str]
) -> None:
    cycle = _create_cycle(db_client, auth)
    cycle_id = cycle["id"]
    assert cycle["as_of_date"] == AS_OF
    assert cycle["due_date"] == "2026-03-31"
    assert len(cycle["sections"]) == 17

    block = db_client.post(
        f"{BASE}/cycles/{cycle_id}/blocks",
        json={"block_type": "capital_position"},
        headers=auth,
    )
    assert block.status_code == 201, block.text
    block_body = block.json()
    assert block_body["status"] == "fresh"
    block_id = block_body["id"]

    document = {
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                "content": [
                    {"type": "text", "text": "The total capital ratio was "},
                    {
                        "type": "factRef",
                        "attrs": {"blockId": block_id, "factKey": "car_pct"},
                    },
                    {"type": "text", "text": " at the year end."},
                ],
            },
            {"type": "dataBlock", "attrs": {"blockId": block_id}},
        ],
    }
    saved = db_client.put(
        f"{BASE}/cycles/{cycle_id}/sections/{SECTION}/working",
        json={"doc": document, "base_rev": 0},
        headers=auth,
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["working_rev"] == 1

    committed = db_client.post(
        f"{BASE}/cycles/{cycle_id}/sections/{SECTION}/versions",
        json={"base_rev": 1, "note": "First pass"},
        headers=auth,
    )
    assert committed.status_code == 201, committed.text
    version = committed.json()
    assert version["version_no"] == 1
    assert version["block_refs"] == [block_id]

    marked = db_client.put(
        f"{BASE}/cycles/{cycle_id}/sections/{SECTION}/requirements/050a",
        json={"status": "met"},
        headers=auth,
    )
    assert marked.status_code == 200
    assert marked.json()["requirement_counts"]["met"] == 1

    readiness = db_client.get(f"{BASE}/cycles/{cycle_id}/readiness", headers=auth)
    assert readiness.status_code == 200
    body = readiness.json()
    assert body["ready_for_freeze"] is False
    assert body["deadline"]["rag"] in {"green", "amber", "red"}
    assert all(item["message"] for item in body["items"])


def test_a_second_tab_gets_a_conflict_rather_than_a_silent_overwrite(
    db_client: TestClient, auth: dict[str, str]
) -> None:
    cycle_id = _create_cycle(db_client, auth)["id"]
    document = {
        "type": "doc",
        "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Mine"}]}],
    }
    first = db_client.put(
        f"{BASE}/cycles/{cycle_id}/sections/{SECTION}/working",
        json={"doc": document, "base_rev": 0},
        headers=auth,
    )
    assert first.status_code == 200
    second = db_client.put(
        f"{BASE}/cycles/{cycle_id}/sections/{SECTION}/working",
        json={"doc": document, "base_rev": 0},
        headers=auth,
    )
    assert second.status_code == 409
    assert "section_rev_conflict" in second.text


def test_the_editor_cannot_smuggle_markup_through_the_api(
    db_client: TestClient, auth: dict[str, str]
) -> None:
    cycle_id = _create_cycle(db_client, auth)["id"]
    response = db_client.put(
        f"{BASE}/cycles/{cycle_id}/sections/{SECTION}/working",
        json={
            "doc": {
                "type": "doc",
                "content": [
                    {
                        "type": "paragraph",
                        "content": [
                            {
                                "type": "text",
                                "text": "x",
                                "marks": [
                                    {"type": "link", "attrs": {"href": "https://example.test"}}
                                ],
                            }
                        ],
                    }
                ],
            },
            "base_rev": 0,
        },
        headers=auth,
    )
    assert response.status_code == 422
    assert "invalid_document" in response.text
    assert "unknown_mark" in response.text


def test_attachments_report_what_is_still_missing(
    db_client: TestClient, auth: dict[str, str]
) -> None:
    cycle_id = _create_cycle(db_client, auth)["id"]
    listing = db_client.get(f"{BASE}/cycles/{cycle_id}/attachments", headers=auth)
    assert listing.status_code == 200
    body = listing.json()
    assert body["attachments"] == []
    report = next(
        entry for entry in body["requirements"] if entry["kind"] == "senior_management_report"
    )
    assert (report["gate"], report["satisfied"]) == ("freeze", False)


def test_the_block_catalogue_is_readable_and_marks_later_phases(
    db_client: TestClient, auth: dict[str, str]
) -> None:
    response = db_client.get(f"{BASE}/block-types", headers=auth)
    assert response.status_code == 200
    by_type = {entry["type"]: entry for entry in response.json()["block_types"]}
    assert by_type["capital_position"]["available"] is True
    assert by_type["risk_register"]["available"] is True
    # P5-D landed the standardised framework resolver; the later-phase marker
    # now sits on P3's review trail.
    assert by_type["irrbb_sf"]["available"] is True
    assert by_type["workflow_summary"]["available"] is False


def test_an_unknown_cycle_is_not_found(db_client: TestClient, auth: dict[str, str]) -> None:
    missing = UUID("00000000-0000-4000-8000-000000000000")
    response = db_client.get(f"{BASE}/cycles/{missing}", headers=auth)
    assert response.status_code == 404
