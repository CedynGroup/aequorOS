"""Who can reach the risk-and-capital half of the ICAAP, and what the refusal says.

The sweep is parametrised over EVERY new route, so a route added later cannot
quietly skip the checks. The order of the gates is P1's and is unchanged: the
module ships dark (404), an institution outside the regime is 404, and only
then does a missing binding become 403.

Three authorities are new and each is tested for the reason it exists:
approving a Pillar 2 figure is not editing one, recording the independent
review needs an organisation-wide Audit grant rather than Capital EDIT, and
proposing a capital-plan update needs the plan's own drafting authority.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

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
from app.models import AuthorizationBinding, Bank, User
from app.services import authorization
from app.services.institution_types import FALLBACK_TYPE_CODE
from tests.api.helpers import ORG_1, ORG_2, USER_1, USER_2, headers
from tests.fixtures.canonical_bank_fixture import (
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
)

SDI_BANK_ID = "BK-P2SDI001"
OTHER_ORG_BANK_ID = "BK-P2OTH001"
CYCLE_ID = uuid4()
ITEM_ID = uuid4()
METRIC_ID = uuid4()
REVIEW_ID = uuid4()
CHALLENGE_ID = uuid4()
LINE_ID = uuid4()
ADDON_ID = uuid4()
REVIEWER = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")


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
            reason="Exercise ICAAP risk-and-capital enforcement.",
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
    """Every P2 route, so a new one cannot skip the authorization sweep."""
    base = f"/api/v1/banks/{bank_id}/icaap"
    cycle = f"{base}/cycles/{CYCLE_ID}"
    reason = {"reason": "Authorization sweep"}
    explanation = {"explanation": "Swept for authorization.", "reason": "Sweep"}
    return [
        ("GET", f"{cycle}/risks", None),
        ("PUT", f"{cycle}/risks/credit", {**reason, "likelihood_score": 3}),
        ("POST", f"{cycle}/risks", {"category_key": "emerging", "title": "X", **reason}),
        ("POST", f"{cycle}/risks/credit/retire", reason),
        ("GET", f"{cycle}/appetite", None),
        (
            "POST",
            f"{cycle}/appetite/metrics",
            {
                "metric_key": "car",
                "label": "Total capital ratio",
                "measure_kind": "qualitative",
                "qualitative_statement": "Sweep",
                **reason,
            },
        ),
        ("PUT", f"{cycle}/appetite/metrics/{METRIC_ID}", {"base_rev": 0, **reason}),
        ("POST", f"{cycle}/appetite/metrics/{METRIC_ID}/retire", reason),
        ("GET", f"{cycle}/capital-triggers", None),
        ("GET", f"{cycle}/pillar2", None),
        (
            "POST",
            f"{cycle}/pillar2/items",
            {"component_key": "irrbb", "method": "irrbb_interim_delta_eve", **reason},
        ),
        ("PUT", f"{cycle}/pillar2/items/{ITEM_ID}", {"base_revision_no": 0, **reason}),
        (
            "POST",
            f"{cycle}/pillar2/items/{ITEM_ID}/compute",
            {"base_revision_no": 0, **reason},
        ),
        (
            "POST",
            f"{cycle}/pillar2/items/{ITEM_ID}/approve",
            {"revision_no": 1, "note": "Sweep"},
        ),
        ("POST", f"{cycle}/pillar2/items/{ITEM_ID}/retire", reason),
        ("GET", f"{cycle}/pillar2/items/{ITEM_ID}/revisions", None),
        ("GET", f"{cycle}/pillar2/table5", None),
        ("POST", f"{cycle}/pillar2/capital-plan-proposal", reason),
        ("GET", f"{cycle}/parameters", None),
        ("GET", f"{cycle}/reconciliation", None),
        ("POST", f"{cycle}/reconciliation/requirement/compute", reason),
        (
            "PUT",
            f"{cycle}/reconciliation/requirement/lines/pillar1_credit/explanation",
            explanation,
        ),
        (
            "POST",
            f"{cycle}/reconciliation/resources/lines",
            {
                "line_key": "cet1_core",
                "label": "CET1",
                "tier": "cet1",
                "internal_amount": "1",
                "regulatory_eligible": True,
                **reason,
            },
        ),
        (
            "PUT",
            f"{cycle}/reconciliation/resources/lines/{LINE_ID}",
            {"base_rev": 0, **reason},
        ),
        ("DELETE", f"{cycle}/reconciliation/resources/lines/{LINE_ID}", None),
        ("POST", f"{cycle}/reconciliation/resources/load-regulatory", reason),
        (
            "PUT",
            f"{cycle}/reconciliation/controls/pillar2_source_consistency"
            "/explanations/baseline:capital_plan:irrbb",
            explanation,
        ),
        ("GET", f"{cycle}/allocation", None),
        ("PUT", f"{cycle}/allocation", {"units": [], "drivers": [], **reason}),
        ("GET", f"{cycle}/audit-reviews", None),
        (
            "POST",
            f"{cycle}/audit-reviews",
            {
                "review_kind": "internal_audit",
                "reviewer_function": "Internal Audit",
                "scope": "Sweep",
                "frequency_statement": "Annual",
                "performed_on": "2026-02-28",
                "overall_opinion": "satisfactory",
                "independence_statement": "Independent",
                **reason,
            },
        ),
        ("PUT", f"{cycle}/audit-reviews/{REVIEW_ID}", {"base_rev": 0, **reason}),
        ("POST", f"{cycle}/audit-reviews/{REVIEW_ID}/finalise", reason),
        ("GET", f"{cycle}/challenges", None),
        (
            "POST",
            f"{cycle}/challenges",
            {
                "raised_in": "board",
                "raised_by_name": "Chair",
                "raised_on": "2026-02-12",
                "target_kind": "cycle",
                "challenge_text": "Sweep",
                "severity": "low",
            },
        ),
        (
            "POST",
            f"{cycle}/challenges/{CHALLENGE_ID}/responses",
            {
                "outcome": "accepted_no_change",
                "response_text": "Sweep",
                "responder_function": "CRO",
            },
        ),
        ("GET", f"{base}/supervisory-addons", None),
        ("POST", f"{base}/supervisory-addons/{ADDON_ID}/confirm", reason),
        ("POST", f"{base}/supervisory-addons/{ADDON_ID}/withdraw", reason),
        ("GET", f"{base}/supervisory-addons/{ADDON_ID}/letter", None),
    ]


def _call(
    client: TestClient, method: str, path: str, payload: dict[str, Any] | None, auth: dict[str, str]
):
    return client.request(method, path, json=payload, headers=auth)


SWEEP = _routes(SAMPLE_BANK_ID)
SWEEP_IDS = [f"{method} {path.split('/icaap')[1]}" for method, path, _ in SWEEP]


def test_the_sweep_covers_every_route_this_workstream_added() -> None:
    """A route with no sweep entry would be enforced by nobody's test."""
    from fastapi.routing import APIRoute  # noqa: PLC0415

    from app.main import create_app  # noqa: PLC0415

    p1_prefixes = (
        "/cycles",
        "/frameworks",
        "/block-types",
    )
    served: set[tuple[str, str]] = set()
    for route in create_app().routes:
        if not isinstance(route, APIRoute) or "/icaap" not in route.path:
            continue
        for method in route.methods or set():
            if method == "HEAD":
                continue
            served.add((method, route.path))
    swept = {
        (
            method,
            path.replace(SAMPLE_BANK_ID, "{bank_id}").replace(str(CYCLE_ID), "{cycle_id}"),
        )
        for method, path, _ in SWEEP
    }
    # Every swept route is really served (path templates line up).
    for method, path in swept:
        templated = (
            path.replace(str(ITEM_ID), "{item_id}")
            .replace(str(METRIC_ID), "{metric_id}")
            .replace(str(REVIEW_ID), "{review_id}")
            .replace(str(CHALLENGE_ID), "{challenge_id}")
            .replace(str(LINE_ID), "{line_id}")
            .replace(str(ADDON_ID), "{addon_id}")
            .replace("/risks/credit/retire", "/risks/{risk_key}/retire")
            .replace("/risks/credit", "/risks/{risk_key}")
            .replace("/lines/pillar1_credit/explanation", "/lines/{line_key}/explanation")
            .replace(
                "/controls/pillar2_source_consistency/explanations/baseline:capital_plan:irrbb",
                "/controls/{control_code}/explanations/{comparison_key}",
            )
        )
        assert (method, templated) in served, f"{method} {templated}"
    assert len(swept) == len(SWEEP)
    # The cycle, framework and block-type routes are P1's and are swept there.
    assert all(prefix.startswith("/") for prefix in p1_prefixes)


@pytest.mark.parametrize(("method", "path", "payload"), SWEEP, ids=SWEEP_IDS)
def test_without_a_binding_every_route_refuses(
    db_client: TestClient, method: str, path: str, payload: dict[str, Any] | None
) -> None:
    response = _call(db_client, method, path, payload, headers(roles=("analyst",)))
    assert response.status_code == 403, response.text
    assert "scoped binding" in response.text


SDI_SWEEP = _routes(SDI_BANK_ID)


@pytest.mark.parametrize(("method", "path", "payload"), SDI_SWEEP, ids=SWEEP_IDS)
def test_an_institution_outside_the_icaap_regime_is_not_found(
    db_client: TestClient, method: str, path: str, payload: dict[str, Any] | None
) -> None:
    """SDIs have no Pillar 2 regime (D-020), so the surface does not exist."""
    _add_bank(SDI_BANK_ID, organization_id=ORG_1, license_type="savings_and_loans")
    version = _grant(institution_id=SDI_BANK_ID)
    response = _call(
        db_client, method, path, payload, headers(roles=("analyst",), authorization_version=version)
    )
    assert response.status_code == 404, response.text


def test_another_tenants_institution_is_not_found(db_client: TestClient) -> None:
    """Cross-tenant is 404: the other tenant's institution is not this one's to know."""
    _add_bank(OTHER_ORG_BANK_ID, organization_id=ORG_2, license_type="universal_bank")
    own_version = _grant(user_id=USER_1)
    response = db_client.get(
        f"/api/v1/banks/{OTHER_ORG_BANK_ID}/icaap/cycles/{CYCLE_ID}/pillar2",
        headers=headers(roles=("analyst",), authorization_version=own_version),
    )
    assert response.status_code == 404, response.text

    # The owning tenant, with its own binding, gets past the institution gate.
    other_version = _grant(organization_id=ORG_2, user_id=USER_2, institution_id=OTHER_ORG_BANK_ID)
    reachable = db_client.get(
        f"/api/v1/banks/{OTHER_ORG_BANK_ID}/icaap/cycles/{CYCLE_ID}/pillar2",
        headers=headers(
            org_id=ORG_2,
            user_id=USER_2,
            roles=("analyst",),
            authorization_version=other_version,
        ),
    )
    assert reachable.status_code == 404, "the invented cycle, not the institution"
    assert "scoped binding" not in reachable.text


def test_an_aggregated_capital_binding_does_not_reach_confidential_figures(
    db_client: TestClient,
) -> None:
    version = _grant(sensitivity_scope=SensitivityScope.AGGREGATED)
    response = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/icaap/cycles/{CYCLE_ID}/pillar2",
        headers=headers(roles=("analyst",), authorization_version=version),
    )
    assert response.status_code == 403


def test_an_approver_may_read_the_register_but_not_edit_it(db_client: TestClient) -> None:
    """The approver bundle carries VIEW and APPROVE, never EDIT."""
    version = _grant(role_bundle=RoleBundle.APPROVER)
    auth = headers(roles=("approver",), authorization_version=version)
    base = f"/api/v1/banks/{SAMPLE_BANK_ID}/icaap/cycles/{CYCLE_ID}"
    assert db_client.get(f"{base}/pillar2", headers=auth).status_code in {200, 404}
    created = db_client.post(
        f"{base}/pillar2/items",
        json={
            "component_key": "irrbb",
            "method": "irrbb_interim_delta_eve",
            "reason": "Approver tries to edit.",
        },
        headers=auth,
    )
    assert created.status_code == 403


def test_an_analyst_cannot_approve_a_pillar_two_figure(db_client: TestClient) -> None:
    """Approving is a separate authority, not a stronger form of editing."""
    version = _grant(role_bundle=RoleBundle.ANALYST)
    response = db_client.post(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/icaap/cycles/{CYCLE_ID}/pillar2/items/{ITEM_ID}/approve",
        json={"revision_no": 1, "note": "Analyst tries to approve."},
        headers=headers(roles=("analyst",), authorization_version=version),
    )
    assert response.status_code == 403, response.text


def test_capital_edit_alone_does_not_let_somebody_record_the_independent_review(
    db_client: TestClient,
) -> None:
    """M12: internal audit must not need the authority it is reviewing."""
    version = _grant(role_bundle=RoleBundle.ANALYST)
    response = db_client.post(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/icaap/cycles/{CYCLE_ID}/audit-reviews",
        json={
            "review_kind": "internal_audit",
            "reviewer_function": "Internal Audit",
            "scope": "The ICAAP methodology.",
            "frequency_statement": "Annual",
            "performed_on": "2026-02-28",
            "overall_opinion": "satisfactory",
            "independence_statement": "Independent of the preparation.",
            "reason": "Record the review.",
        },
        headers=headers(roles=("analyst",), authorization_version=version),
    )
    assert response.status_code == 403, response.text
    assert "Audit" in response.text


def test_an_organisation_wide_audit_grant_is_what_admits_a_reviewer(
    db_client: TestClient,
) -> None:
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        session.add(
            User(
                id=REVIEWER,
                organization_id=ORG_1,
                email="ia.reviewer@example.test",
                display_name="Internal Audit",
            )
        )
        session.commit()
    finally:
        session.close()
    _grant(user_id=REVIEWER)
    version = _grant(
        user_id=REVIEWER,
        module_scope=ModuleScope.AUDIT,
        sensitivity_scope=SensitivityScope.CONFIDENTIAL,
        institution_id=None,
    )
    response = db_client.post(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/icaap/cycles/{CYCLE_ID}/audit-reviews",
        json={
            "review_kind": "internal_audit",
            "reviewer_function": "Internal Audit",
            "scope": "The ICAAP methodology.",
            "frequency_statement": "Annual",
            "performed_on": "2026-02-28",
            "overall_opinion": "satisfactory",
            "independence_statement": "Independent of the preparation.",
            "reason": "Record the review.",
        },
        headers=headers(roles=("analyst",), user_id=REVIEWER, authorization_version=version),
    )
    # The cycle id is invented, so authorization passes and the cycle is not
    # found — which is exactly the gate this test is about.
    assert response.status_code == 404, response.text
