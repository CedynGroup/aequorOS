"""The AI drafting routes over HTTP: who may ask, and what they are told.

The 404-versus-403 split is the point. A platform with AI switched off has no AI
surface at all — 404, before any tenant is resolved, so the route answers the
same to everyone. A platform that HAS the surface but whose tenant has not
switched it on answers 403 with a code the dashboard can turn into a sentence,
because that is something the tenant can act on.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.core.authorization import (
    GrantorType,
    InstitutionScope,
    ModuleScope,
    PrincipalType,
    RoleBundle,
    SensitivityScope,
)
from app.core.config import get_settings
from app.db.base import utc_now
from app.db.session import get_sessionmaker
from app.models import AuthorizationBinding, BankReportingPeriod, User
from app.models.ai import AiCommentarySettings
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
def ai_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_COMMENTARY_ENABLED", "1")
    get_settings.cache_clear()


def _seed(*, bundle: RoleBundle, ai_enabled: bool) -> dict[str, str]:
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
            role_bundle=bundle,
            scope=authorization.BindingScope(
                InstitutionScope.INSTITUTION,
                SAMPLE_BANK_ID,
                ModuleScope.CAPITAL,
                SensitivityScope.CONFIDENTIAL,
            ),
            grantor=authorization.GrantorRef(GrantorType.SYSTEM, "test-suite"),
            reason="Exercise the ICAAP AI drafting routes over HTTP.",
        )
        period = session.scalar(
            select(BankReportingPeriod.id).where(
                BankReportingPeriod.bank_id == SAMPLE_BANK_ID,
                BankReportingPeriod.period_end == AS_OF,
            )
        )
        assert period is not None
        from app.api.deps import TenantContext  # noqa: PLC0415

        regulatory_capital.create_capital_run(
            session,
            TenantContext(organization_id=ORG_1, actor_user_id=USER_1, authorization_version=1),
            SAMPLE_BANK_ID,
            RegulatoryRunCreate(
                module="capital", reporting_period_id=period, scenario_code="baseline"
            ),
        )
        if ai_enabled:
            session.add(
                AiCommentarySettings(
                    organization_id=ORG_1,
                    enabled=True,
                    enabled_features=["icaap_drafting"],
                    descriptor_only=False,
                    consent_version=get_settings().ai.consent_version,
                    consented_by=USER_1,
                    consented_at=utc_now(),
                    updated_by=USER_1,
                )
            )
        session.commit()
        user = session.get(User, USER_1)
        assert user is not None
        session.refresh(user)
        role = "analyst" if bundle is RoleBundle.ANALYST else "approver"
        return headers(roles=(role,), authorization_version=user.authorization_version)
    finally:
        session.close()


@pytest.fixture
def analyst(db_client: TestClient) -> dict[str, str]:
    _ = db_client
    return _seed(bundle=RoleBundle.ANALYST, ai_enabled=True)


@pytest.fixture
def analyst_without_consent(db_client: TestClient) -> dict[str, str]:
    _ = db_client
    return _seed(bundle=RoleBundle.ANALYST, ai_enabled=False)


def _rebind(bundle: RoleBundle) -> dict[str, str]:
    """Swap the caller's bundle without disturbing the seeded cycle."""
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        session.execute(
            delete(AuthorizationBinding).where(AuthorizationBinding.organization_id == ORG_1)
        )
        authorization.create_role_binding(
            session,
            organization_id=ORG_1,
            principal_user_id=USER_1,
            principal_type=PrincipalType.HUMAN,
            role_bundle=bundle,
            scope=authorization.BindingScope(
                InstitutionScope.INSTITUTION,
                SAMPLE_BANK_ID,
                ModuleScope.CAPITAL,
                SensitivityScope.CONFIDENTIAL,
            ),
            grantor=authorization.GrantorRef(GrantorType.SYSTEM, "test-suite"),
            reason="Read an AI draft as a checker.",
        )
        session.commit()
        user = session.get(User, USER_1)
        assert user is not None
        session.refresh(user)
        return headers(roles=("approver",), authorization_version=user.authorization_version)
    finally:
        session.close()


def _cycle(client: TestClient, auth: dict[str, str]) -> dict[str, Any]:
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


def _linked_cycle(client: TestClient, auth: dict[str, str]) -> str:
    cycle = _cycle(client, auth)
    created = client.post(
        f"{BASE}/cycles/{cycle['id']}/blocks",
        json={"block_type": "capital_position"},
        headers=auth,
    )
    assert created.status_code == 201, created.text
    return cycle["id"]


def _code(response: Any) -> str:
    """The machine code out of the platform's error envelope."""
    return response.json()["error"]["details"]["error_code"]


def _ai_path(cycle_id: str, suffix: str = "") -> str:
    return f"{BASE}/cycles/{cycle_id}/sections/{SECTION}/ai-drafts{suffix}"


def test_an_analyst_can_request_a_draft(db_client: TestClient, analyst: dict[str, str]) -> None:
    cycle_id = _linked_cycle(db_client, analyst)
    response = db_client.post(_ai_path(cycle_id), headers=analyst)
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "queued"
    assert body["draft"] is None
    assert body["poll_after_seconds"] == get_settings().ai.client_poll_seconds


def test_a_second_request_is_debounced_and_answers_200(
    db_client: TestClient, analyst: dict[str, str]
) -> None:
    cycle_id = _linked_cycle(db_client, analyst)
    assert db_client.post(_ai_path(cycle_id), headers=analyst).status_code == 202
    again = db_client.post(_ai_path(cycle_id), headers=analyst)
    assert again.status_code == 200


def test_the_surface_does_not_exist_when_the_platform_has_ai_switched_off(
    db_client: TestClient, analyst: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """404 before any tenant lookup: the same answer for everyone."""
    cycle_id = _linked_cycle(db_client, analyst)
    monkeypatch.setenv("AI_COMMENTARY_ENABLED", "0")
    get_settings.cache_clear()
    response = db_client.post(_ai_path(cycle_id), headers=analyst)
    assert response.status_code == 404


def test_a_tenant_that_has_not_switched_it_on_is_told_so(
    db_client: TestClient, analyst_without_consent: dict[str, str]
) -> None:
    """403 with a code: this is something the tenant's Owner can act on."""
    cycle_id = _linked_cycle(db_client, analyst_without_consent)
    response = db_client.post(_ai_path(cycle_id), headers=analyst_without_consent)
    assert response.status_code == 403
    assert _code(response) == "tenant_disabled"


def test_an_approver_may_read_drafts_but_never_request_one(
    db_client: TestClient, analyst: dict[str, str]
) -> None:
    """Maker and checker stay apart at the route, not inside a service."""
    cycle_id = _linked_cycle(db_client, analyst)
    approver = _rebind(RoleBundle.APPROVER)
    assert db_client.get(_ai_path(cycle_id), headers=approver).status_code == 200
    assert db_client.post(_ai_path(cycle_id), headers=approver).status_code == 403


def test_a_section_with_no_linked_figures_is_refused_with_a_reason(
    db_client: TestClient, analyst: dict[str, str]
) -> None:
    cycle_id = _cycle(db_client, analyst)["id"]
    response = db_client.post(_ai_path(cycle_id), headers=analyst)
    assert response.status_code == 409
    assert _code(response) == "no_usable_facts"


def test_a_cross_tenant_bank_is_not_found(db_client: TestClient, analyst: dict[str, str]) -> None:
    response = db_client.post(
        "/api/v1/banks/BK-NOTYOURS/icaap/cycles/"
        "0199a3c0-0000-7000-8000-000000000001/sections/executive_summary/ai-drafts",
        headers=analyst,
    )
    assert response.status_code == 404


def test_an_unknown_suggestion_is_not_found(db_client: TestClient, analyst: dict[str, str]) -> None:
    cycle_id = _linked_cycle(db_client, analyst)
    response = db_client.get(
        _ai_path(cycle_id, "/0199a3c0-0000-7000-8000-00000000dead"), headers=analyst
    )
    assert response.status_code == 404


def test_listing_is_empty_before_anything_is_requested(
    db_client: TestClient, analyst: dict[str, str]
) -> None:
    cycle_id = _linked_cycle(db_client, analyst)
    response = db_client.get(_ai_path(cycle_id), headers=analyst)
    assert response.status_code == 200
    assert response.json()["items"] == []


def test_an_impersonated_examiner_cannot_request_a_draft(
    db_client: TestClient, analyst: dict[str, str]
) -> None:
    """A supervisor reading a frozen report must not be able to spend the
    bank's AI budget, or put text into the bank's own document."""
    from tests.api.test_impersonation_boundary import (  # noqa: PLC0415
        _impersonation_headers,
    )

    cycle_id = _linked_cycle(db_client, analyst)
    examiner = _impersonation_headers()
    assert db_client.post(_ai_path(cycle_id), headers=examiner).status_code in {401, 403}
