"""Who can reach the ICAAP workspace, and what the refusal says.

The order of the gates is the policy, and each step is tested for the reason it
exists:

* the module ships dark, and a deployment that has not enabled it answers 404
  everywhere rather than advertising a surface that is not there;
* an institution outside the ICAAP regime is 404, not 403 — saying "forbidden"
  would imply the surface might apply to it;
* a caller with no binding gets 403, because their own institution existing is
  not a secret from them.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.routing import APIRoute
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
from app.main import create_app
from app.models import AuthorizationBinding, Bank, User
from app.services import authorization
from app.services.institution_types import FALLBACK_TYPE_CODE
from tests.api.helpers import ORG_1, ORG_2, USER_1, headers
from tests.fixtures.canonical_bank_fixture import (
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
)

SDI_BANK_ID = "BK-ICSDI001"
OTHER_ORG_BANK_ID = "BK-ICOTH001"
CYCLE_ID = uuid4()
BLOCK_ID = uuid4()
ATTACHMENT_ID = uuid4()


@pytest.fixture(autouse=True)
def _enable_workspace(monkeypatch: pytest.MonkeyPatch) -> None:
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _start_without_fixture_authority(db_client: TestClient) -> None:
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        session.execute(
            delete(AuthorizationBinding).where(AuthorizationBinding.organization_id == ORG_1)
        )
        materialize_canonical_test_book(session)
        session.commit()
    finally:
        session.close()


def _grant(  # noqa: PLR0913 - every binding dimension is an enforcement input
    *,
    role_bundle: RoleBundle = RoleBundle.ANALYST,
    module_scope: ModuleScope = ModuleScope.CAPITAL,
    sensitivity_scope: SensitivityScope = SensitivityScope.CONFIDENTIAL,
    institution_id: str | None = SAMPLE_BANK_ID,
    organization_id: str = ORG_1,
    user_id: UUID = USER_1,
) -> int:
    session = get_sessionmaker()()
    session.info["organization_id"] = organization_id
    try:
        authorization.create_role_binding(
            session,
            organization_id=organization_id,
            principal_user_id=user_id,
            principal_type=PrincipalType.HUMAN,
            role_bundle=role_bundle,
            scope=authorization.BindingScope(
                InstitutionScope.INSTITUTION if institution_id else InstitutionScope.ORGANIZATION,
                institution_id,
                module_scope,
                sensitivity_scope,
            ),
            grantor=authorization.GrantorRef(GrantorType.SYSTEM, "test-suite"),
            reason="Exercise ICAAP scoped-binding enforcement.",
        )
        user = session.get(User, user_id)
        assert user is not None
        session.refresh(user)
        return user.authorization_version
    finally:
        session.close()


def _add_bank(bank_id: str, *, organization_id: str, license_type: str) -> None:
    session = get_sessionmaker()()
    session.info["organization_id"] = organization_id
    try:
        session.add(
            Bank(
                id=bank_id,
                organization_id=organization_id,
                name=f"Fixture {bank_id}",
                short_name="Fixture",
                currency="GHS",
                jurisdiction_code="GH",
                license_type=license_type,
                institution_type=(
                    "savings_and_loans"
                    if license_type == "savings_and_loans"
                    else FALLBACK_TYPE_CODE
                ),
            )
        )
        session.commit()
    finally:
        session.close()


def _routes(bank_id: str) -> list[tuple[str, str, dict[str, Any] | None]]:
    """Every ICAAP route, so a new one cannot skip the authorization sweep."""
    base = f"/api/v1/banks/{bank_id}/icaap"
    cycle = f"{base}/cycles/{CYCLE_ID}"
    create_payload = {
        "fiscal_year": 2025,
        "cycle_kind": "rehearsal",
        "basis": "solo",
        "framework_code": "bog_icaap",
        "framework_version": "2026.02-ed.1",
        "reason": "Authorization sweep",
    }
    return [
        ("GET", f"{base}/frameworks", None),
        ("GET", f"{base}/frameworks/bog_icaap/versions/2026.02-ed.1", None),
        ("GET", f"{base}/block-types", None),
        ("GET", f"{base}/cycles", None),
        ("POST", f"{base}/cycles", create_payload),
        ("GET", cycle, None),
        ("PATCH", cycle, {"reason": "Sweep"}),
        ("POST", f"{cycle}/archive", {"reason": "Sweep"}),
        (
            "POST",
            f"{cycle}/rebase",
            {
                "framework_code": "bog_icaap",
                "framework_version": "2026.02-ed.1",
                "reason": "Sweep",
            },
        ),
        ("GET", f"{cycle}/readiness", None),
        ("GET", f"{cycle}/sections", None),
        ("GET", f"{cycle}/sections/executive_summary", None),
        (
            "PUT",
            f"{cycle}/sections/executive_summary/working",
            {"doc": {"type": "doc", "content": []}, "base_rev": 0},
        ),
        ("POST", f"{cycle}/sections/executive_summary/versions", {"base_rev": 0}),
        ("GET", f"{cycle}/sections/executive_summary/versions", None),
        ("GET", f"{cycle}/sections/executive_summary/versions/1", None),
        (
            "PUT",
            f"{cycle}/sections/executive_summary/requirements/050a",
            {"status": "met"},
        ),
        ("GET", f"{cycle}/blocks", None),
        ("POST", f"{cycle}/blocks", {"block_type": "capital_position"}),
        ("GET", f"{cycle}/blocks/{BLOCK_ID}", None),
        ("POST", f"{cycle}/blocks/{BLOCK_ID}/refresh", {}),
        ("GET", f"{cycle}/blocks/{BLOCK_ID}/bindings", None),
        ("POST", f"{cycle}/blocks/{BLOCK_ID}/pin", {"reason": "A recorded judgement here."}),
        ("DELETE", f"{cycle}/blocks/{BLOCK_ID}/pin", None),
        (
            "PUT",
            f"{cycle}/blocks/{BLOCK_ID}/manual-table",
            {
                "columns": [{"key": "fy0", "label": "2025", "kind": "amount"}],
                "rows": [],
                "reason": "Sweep",
            },
        ),
        ("POST", f"{cycle}/blocks/{BLOCK_ID}/retire", {"reason": "Sweep"}),
        ("GET", f"{cycle}/attachments", None),
        ("GET", f"{cycle}/attachments/{ATTACHMENT_ID}/download", None),
        ("POST", f"{cycle}/attachments/{ATTACHMENT_ID}/withdraw", {"reason": "Sweep"}),
    ]


def _call(
    client: TestClient, method: str, path: str, payload: dict[str, Any] | None, auth: dict[str, str]
):
    return client.request(method, path, json=payload, headers=auth)


SWEEP = _routes(SAMPLE_BANK_ID)
SWEEP_IDS = [f"{method} {path.split('/icaap')[1]}" for method, path, _ in SWEEP]


@pytest.mark.parametrize(("method", "path", "payload"), SWEEP, ids=SWEEP_IDS)
def test_without_a_binding_every_route_refuses(
    db_client: TestClient, method: str, path: str, payload: dict[str, Any] | None
) -> None:
    response = _call(db_client, method, path, payload, headers(roles=("analyst",)))
    assert response.status_code == 403, response.text
    assert "active scoped binding" in response.text


def test_a_capital_binding_at_the_wrong_classification_is_not_enough(
    db_client: TestClient,
) -> None:
    """ICAAP is confidential: an aggregated capital view does not reach it."""
    version = _grant(sensitivity_scope=SensitivityScope.AGGREGATED)
    response = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/icaap/cycles",
        headers=headers(roles=("analyst",), authorization_version=version),
    )
    assert response.status_code == 403


def test_a_binding_on_another_institution_does_not_carry(db_client: TestClient) -> None:
    _add_bank("BK-ICSIB001", organization_id=ORG_1, license_type="universal_bank")
    version = _grant(institution_id="BK-ICSIB001")
    response = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/icaap/cycles",
        headers=headers(roles=("analyst",), authorization_version=version),
    )
    assert response.status_code == 403


def test_an_analyst_may_read_and_write(db_client: TestClient) -> None:
    version = _grant(role_bundle=RoleBundle.ANALYST)
    auth = headers(roles=("analyst",), authorization_version=version)
    listing = db_client.get(f"/api/v1/banks/{SAMPLE_BANK_ID}/icaap/cycles", headers=auth)
    assert listing.status_code == 200
    created = db_client.post(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/icaap/cycles",
        json={
            "fiscal_year": 2025,
            "cycle_kind": "rehearsal",
            "basis": "solo",
            "framework_code": "bog_icaap",
            "framework_version": "2026.02-ed.1",
            "reason": "Dry run",
        },
        headers=auth,
    )
    assert created.status_code == 201, created.text


def test_an_approver_may_read_but_not_write(db_client: TestClient) -> None:
    """The approver bundle carries no CREATE or EDIT: maker and checker differ."""
    version = _grant(role_bundle=RoleBundle.APPROVER)
    auth = headers(roles=("approver",), authorization_version=version)
    assert (
        db_client.get(f"/api/v1/banks/{SAMPLE_BANK_ID}/icaap/cycles", headers=auth).status_code
        == 200
    )
    created = db_client.post(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/icaap/cycles",
        json={
            "fiscal_year": 2025,
            "cycle_kind": "rehearsal",
            "basis": "solo",
            "framework_code": "bog_icaap",
            "framework_version": "2026.02-ed.1",
            "reason": "Dry run",
        },
        headers=auth,
    )
    assert created.status_code == 403


def test_a_viewer_may_read_and_not_export(db_client: TestClient) -> None:
    version = _grant(role_bundle=RoleBundle.VIEWER)
    auth = headers(roles=("viewer",), authorization_version=version)
    assert (
        db_client.get(f"/api/v1/banks/{SAMPLE_BANK_ID}/icaap/cycles", headers=auth).status_code
        == 200
    )
    export = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/icaap/cycles/{CYCLE_ID}/draft.pdf", headers=auth
    )
    assert export.status_code == 403


def test_an_institution_outside_the_icaap_regime_has_no_such_surface(
    db_client: TestClient,
) -> None:
    """A deposit-taking institution outside the regime sees 404, not 403."""
    _add_bank(SDI_BANK_ID, organization_id=ORG_1, license_type="savings_and_loans")
    version = _grant(institution_id=SDI_BANK_ID)
    auth = headers(roles=("analyst",), authorization_version=version)
    for method, path, payload in _routes(SDI_BANK_ID):
        response = _call(db_client, method, path, payload, auth)
        assert response.status_code == 404, f"{method} {path}: {response.text}"


def test_another_tenants_institution_does_not_exist_here(db_client: TestClient) -> None:
    _add_bank(OTHER_ORG_BANK_ID, organization_id=ORG_2, license_type="universal_bank")
    version = _grant()
    response = db_client.get(
        f"/api/v1/banks/{OTHER_ORG_BANK_ID}/icaap/cycles",
        headers=headers(roles=("analyst",), authorization_version=version),
    )
    assert response.status_code == 404


def test_every_icaap_route_is_on_a_scoped_dependency() -> None:
    """A new route cannot quietly fall back to the scalar role ladder."""
    routes = [
        route
        for route in create_app().routes
        if isinstance(route, APIRoute) and "/icaap/" in route.path
    ]
    assert len(routes) >= 30
    for route in routes:
        names: set[str] = set()
        stack = [route.dependant]
        while stack:
            dependant = stack.pop()
            if dependant.call is not None:
                names.add(getattr(dependant.call, "__name__", ""))
            stack.extend(dependant.dependencies)
        scoped = {name for name in names if name.startswith("require_icaap_")}
        assert len(scoped) == 1, f"{route.methods} {route.path}: {sorted(scoped)}"
        assert "get_mutation_tenant_context" not in names
        assert "get_approver_tenant_context" not in names
        assert not any(name.startswith("require_role_") for name in names)
        assert "require_module_access" not in names
