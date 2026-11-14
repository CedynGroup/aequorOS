"""The mutation boundary: P0-2 (read-guarded mutations) and P0-3 (document delete).

Two invariants, enforced in two different places, tested here together because
they share one failure mode — a route author picking ``ctx: Tenant`` for a
mutation:

1. **Absolute, structural.** An act-as-examiner impersonation session may NEVER
   answer an unsafe HTTP method. Enforced at the authentication boundary
   (``deps.refuse_impersonated_mutation``, called from ``get_current_principal``
   and again from ``get_tenant_db_session``), so it holds regardless of which
   ``ctx`` dependency a route declares. The one documented exemption is
   ``deps.IMPERSONATION_READ_ONLY_ROUTES``.
2. **Role ladder.** ``viewer`` and ``examiner`` are read-only. This one is NOT
   structural — it is carried by the route's own ``MutationTenant`` declaration —
   so the route-table sweep below is what keeps it honest: it is the test that
   would have caught P0-2.

The earlier suite (``test_impersonation_auth.py``) sampled five paths that all
already used ``MutationTenant``, so it could not see the defect. This one walks
the whole table.

This module's other-suite cases live in ``test_impersonation_boundary.py``.
"""

from __future__ import annotations

__all__ = ["_unsafe_routes"]
import datetime as dt
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.api.deps import IMPERSONATION_READ_ONLY_ROUTES
from app.core import security
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
from app.identity.service import authorization
from app.integrations.storage.base import StoredObjectHead
from app.models import Bank, RegulatoryPackage, User
from app.policy.service.institution_types import FALLBACK_TYPE_CODE
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID, materialize_canonical_test_book
from tests.support.api_factories import CaseFactory, DocumentFactory
from tests.support.helpers import ORG_1, USER_1, headers

_UNSAFE_METHODS = ("POST", "PUT", "PATCH", "DELETE")
_SUBMISSION_BANK_ID = "BK-IMPBND01"
# The 14 routes this change moved off the read guard (audit P0-2 / P0-3). Pinned
# by templated path so a future edit that reverts one is caught by name.
FIXED_MUTATION_ROUTES: tuple[tuple[str, str], ...] = (
    ("POST", "/api/v1/cases/bulk-actions"),
    ("POST", "/api/v1/documents/upload-request"),
    ("POST", "/api/v1/documents/{document_id}/complete-upload"),
    ("POST", "/api/v1/documents/{document_id}/parse"),
    ("DELETE", "/api/v1/documents/{document_id}"),
    ("POST", "/api/v1/cases/{case_id}/financial-workspace/map"),
    ("POST", "/api/v1/cases/{case_id}/financial-data/validate"),
    ("POST", "/api/v1/cases/{case_id}/decisions"),
    ("POST", "/api/v1/cases"),
    ("PATCH", "/api/v1/cases/{case_id}"),
    ("POST", "/api/v1/cases/{case_id}/assign"),
    ("POST", "/api/v1/cases/{case_id}/archive"),
    ("POST", "/api/v1/assessments"),
    ("POST", "/api/v1/assessments/{assessment_id}/run"),
)


# --- route-table introspection ------------------------------------------------
def _dependency_names(route: APIRoute) -> set[str]:
    names: set[str] = set()
    stack = [route.dependant]
    while stack:
        dependant = stack.pop()
        if dependant.call is not None:
            names.add(getattr(dependant.call, "__name__", ""))
        stack.extend(dependant.dependencies)
    return names


def _unsafe_routes(client: TestClient) -> list[tuple[str, str, set[str]]]:
    """(method, templated path, dependency names) for every unsafe-method route."""
    app = cast("FastAPI", client.app)
    rows: list[tuple[str, str, set[str]]] = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        names = _dependency_names(route)
        for method in sorted(set(route.methods or set()) & set(_UNSAFE_METHODS)):
            rows.append((method, route.path, names))
    return rows


def _fill(path: str) -> str:
    """Substitute placeholder values into a templated path."""
    out = path
    while "{" in out:
        start = out.index("{")
        end = out.index("}", start)
        name = out[start + 1 : end]
        if name.endswith("bank_id"):
            value = "BK-IMPRSN01"
        elif name == "module":
            value = "liquidity"
        elif name.endswith(("_id", "id")):
            value = str(uuid4())
        else:
            value = "x"
        out = out[:start] + value + out[end + 1 :]
    return out


# --- credentials --------------------------------------------------------------
def _impersonation_headers(org_id: str = ORG_1) -> dict[str, str]:
    secret = get_settings().auth.impersonation_jwt_secret
    assert secret, "the impersonation secret must be configured for this suite"
    now = utc_now()
    token = security.mint_impersonation_token(
        organization_id=org_id,
        act_operator="ops@aequoros.com",
        session_id="11111111-2222-4333-8444-555555555555",
        secret=secret,
        issued_at=now,
        expires_at=now + dt.timedelta(minutes=15),
    )
    return {"Authorization": f"Bearer {token}"}


def _materialize_sample_bank() -> None:
    session = get_sessionmaker()()
    try:
        materialize_canonical_test_book(session)
        session.commit()
    finally:
        session.close()


def _call(client: TestClient, method: str, path: str, hdrs: dict[str, str]) -> Any:
    return client.request(method, path, headers=hdrs, json={})


def _submission_fixture() -> UUID:
    """One bank and one ordinary (ungated) package, for the submission gate."""
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        if session.get(Bank, _SUBMISSION_BANK_ID) is None:
            session.add(
                Bank(
                    id=_SUBMISSION_BANK_ID,
                    organization_id=ORG_1,
                    name="Submission Boundary Bank",
                    short_name="Submission",
                    currency="GHS",
                    jurisdiction_code="GH",
                    license_type="universal",
                    institution_type=FALLBACK_TYPE_CODE,
                )
            )
            session.commit()
        package = RegulatoryPackage(
            organization_id=ORG_1,
            bank_id=_SUBMISSION_BANK_ID,
            return_family="liquidity",
            return_code="LCR-NSFR",
            reporting_date=dt.date(2026, 3, 31),
            frequency="monthly",
            basis="solo",
            status="approved",
            version=1,
            snapshot={"sections": []},
            source_runs=[],
            generated_by=uuid4(),
            generated_at=utc_now(),
        )
        session.add(package)
        session.commit()
        return package.id
    finally:
        session.close()


def _grant_institution_coverage(bundle: RoleBundle, module: ModuleScope) -> int:
    """One institution-scoped binding on the submission bank; returns the new authv."""
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        authorization.create_role_binding(
            session,
            organization_id=ORG_1,
            principal_user_id=USER_1,
            principal_type=PrincipalType.HUMAN,
            role_bundle=bundle,
            scope=authorization.BindingScope(
                InstitutionScope.INSTITUTION,
                _SUBMISSION_BANK_ID,
                module,
                SensitivityScope.RESTRICTED,
            ),
            grantor=authorization.GrantorRef(GrantorType.SYSTEM, "test-suite"),
            reason="exercise the submission gate's coverage/authority split",
        )
        user = session.get(User, USER_1)
        assert user is not None
        session.refresh(user)
        return user.authorization_version
    finally:
        session.close()


def test_account_admin_cannot_use_the_regulatory_submission_route(
    db_client: TestClient,
) -> None:
    """The legacy admin split must remove operational superuser authority.

    REWRITTEN 2026-09-20. This test used to assert 403 on a package that does
    not exist, and its docstring said why: "the role dependency runs before the
    workflow lookup". That was true while submit was gated by the scalar approver
    dependency, and it is the contract the filing cutover removed —
    transmission is now a scoped authority, so the route resolves the resource
    first like every other binding-enforced package route.

    The refusal therefore has two shapes, and both are asserted here because
    asserting one is how the distinction drifts back (D-073, and the same rule
    AGENTS.md states for bank detail/period/fact routes):

    * **a package the route cannot resolve for this caller -> 404**, and it is
      404 whether the package is absent or the caller has no coverage of the
      institution. Nothing about what exists is confirmed before authority is.
      (The coverage half of that rule is binding-derived only for a GATED
      family, and is pinned where it lives:
      ``test_package_authorization.py::test_a_gated_family_still_hides_before_it_refuses``.)
    * **a resolvable package, the action's authority missing -> 403**, naming
      what is missing. The caller's own institution is not a secret from them.
      An account administrator is never told to go and get the ``analyst`` role,
      because no scalar role supplies filing authority any more.

    The bank is materialised BEFORE the first call (#208): tenant-bank
    resolution precedes every authority dependency, so without it the 404 would
    be about the institution and would prove nothing about the package.
    """

    package_id = _submission_fixture()
    absent = db_client.post(
        f"/api/v1/banks/{_SUBMISSION_BANK_ID}/regulatory-packages/{uuid4()}/submit",
        headers=headers(roles=("account_admin",)),
        json={"channel": "email"},
    )
    assert absent.status_code == 404, absent.text

    # A package that does resolve, and coverage of its institution — from a
    # bundle that carries `view` over the filing scope but not `submit`.
    authv = _grant_institution_coverage(RoleBundle.VIEWER, ModuleScope.REGULATORY)
    refused = db_client.post(
        f"/api/v1/banks/{_SUBMISSION_BANK_ID}/regulatory-packages/{package_id}/submit",
        headers=headers(roles=("account_admin",), authorization_version=authv),
        json={"channel": "email"},
    )
    assert refused.status_code == 403, refused.text
    message = refused.json()["error"]["message"]
    assert "Validator" in message
    assert "analyst" not in message


def test_account_admin_is_limited_to_account_administration(db_client: TestClient) -> None:
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        authorization.create_role_binding(
            session,
            organization_id=ORG_1,
            principal_user_id=USER_1,
            principal_type=PrincipalType.HUMAN,
            role_bundle=RoleBundle.ACCOUNT_ADMIN,
            scope=authorization.BindingScope(
                InstitutionScope.ORGANIZATION,
                None,
                ModuleScope.ACCOUNT,
                SensitivityScope.RESTRICTED,
            ),
            grantor=authorization.GrantorRef(GrantorType.SYSTEM, "test-suite"),
            reason="exercise account-only administration",
        )
        user = session.get(User, USER_1)
        assert user is not None
        account_headers = headers(
            roles=("account_admin",),
            authorization_version=user.authorization_version,
        )
    finally:
        session.close()

    integration_keys = db_client.get("/api/v1/integration-keys", headers=account_headers)
    assert integration_keys.status_code == 200, integration_keys.text

    # A valid payload is required so the request reaches the service-level
    # organization-scope REG configure gate (the dependency no longer denies).
    attestation_templates = db_client.put(
        "/api/v1/attestation/signature-placements",
        headers=account_headers,
        json={
            "return_code": "LCR-NSFR",
            "placements": [],
            "reason": "account-admin must not author placement templates",
        },
    )
    assert attestation_templates.status_code == 403
    assert (
        "organization-scoped Regulatory Reporting"
        in attestation_templates.json()["error"]["message"]
    )


# --- 2. impersonation cannot mutate ANYTHING ----------------------------------
def test_impersonated_examiner_cannot_reach_any_unsafe_method_route(
    db_client: TestClient,
) -> None:
    """Enumerated from the app's own route table, not hand-picked.

    Every authenticated unsafe-method route must answer 403 to an impersonation
    token — including the 14 that were behind the read guard, which the previous
    suite's five hand-picked paths could not have shown.
    """
    hdrs = _impersonation_headers()
    checked = 0
    failures: list[tuple[str, str, int]] = []
    for method, path, names in _unsafe_routes(db_client):
        if "get_current_principal" not in names:
            continue
        if (method, path) in IMPERSONATION_READ_ONLY_ROUTES:
            continue
        response = _call(db_client, method, _fill(path), hdrs)
        checked += 1
        if response.status_code != 403:
            failures.append((method, path, response.status_code))
    assert failures == [], f"impersonation reached a mutation: {failures}"
    # Guard against the sweep silently degenerating to zero routes.
    assert checked > 150, f"only {checked} routes swept"


@pytest.mark.parametrize(("method", "path"), FIXED_MUTATION_ROUTES)
def test_impersonated_examiner_blocked_on_each_audited_route(
    db_client: TestClient, method: str, path: str
) -> None:
    response = _call(db_client, method, _fill(path), _impersonation_headers())
    assert response.status_code == 403, response.text
    assert "read-only" in response.json()["error"]["message"].lower()


def test_read_only_compute_post_still_works_under_impersonation(
    db_client: TestClient,
) -> None:
    """The documented exemption is not silently broken by the boundary guard.

    The scenario-workbench what-if is a POST only because its input does not fit
    in a query string; it writes nothing. It must stay reachable — an operator
    inspecting a tenant needs it. A missing bank yields the handler's own
    404/422, which proves the guard admitted the request.
    """
    response = db_client.post(
        "/api/v1/banks/BK-IMPRSN01/scenario-workbench/liquidity/analysis",
        headers=_impersonation_headers(),
        json={},
    )
    assert response.status_code != 403, response.text
    assert response.status_code in (404, 422), response.text


def test_impersonated_examiner_can_still_read(
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: the boundary guard touches only unsafe methods."""
    session = get_sessionmaker()()
    try:
        session.add(
            Bank(
                id="BK-IMPRSN01",
                organization_id=ORG_1,
                name="Inspection Bank",
                short_name="Inspection",
                currency="GHS",
                jurisdiction_code="GH",
                license_type="universal_bank",
                institution_type=FALLBACK_TYPE_CODE,
            )
        )
        session.commit()
    finally:
        session.close()
    impersonation_headers = _impersonation_headers()
    banks = db_client.get("/api/v1/banks", headers=impersonation_headers)
    authority = db_client.get(
        "/api/v1/auth/effective-authority",
        headers=impersonation_headers,
    )
    profile = db_client.get("/api/v1/auth/me", headers=impersonation_headers)

    assert banks.status_code == 200, banks.text
    assert authority.status_code == 200, authority.text
    assert authority.json()["authv"] == 0
    assert authority.json()["organization_capabilities"] == []
    assert [
        institution["institution_id"]
        for institution in authority.json()["institution_capabilities"]
    ] == ["BK-IMPRSN01"]
    assert all(
        capability["permission"] == "view"
        and capability["requires_contextual_authorization"] is False
        for institution in authority.json()["institution_capabilities"]
        for capability in institution["capabilities"]
    )
    assert profile.status_code == 401, profile.text

    monkeypatch.setenv("DEMO_MODE", "1")
    get_settings.cache_clear()
    hidden_banks = db_client.get("/api/v1/banks", headers=impersonation_headers)
    hidden_detail = db_client.get(
        "/api/v1/banks/BK-IMPRSN01",
        headers=impersonation_headers,
    )
    hidden_periods = db_client.get(
        "/api/v1/banks/BK-IMPRSN01/reporting-periods",
        headers=impersonation_headers,
    )
    hidden_facts = db_client.get(
        f"/api/v1/banks/BK-IMPRSN01/reporting-periods/{uuid4()}/facts",
        headers=impersonation_headers,
    )

    assert hidden_banks.status_code == 200, hidden_banks.text
    assert hidden_banks.json()["banks"] == []
    assert hidden_detail.status_code == 404, hidden_detail.text
    assert hidden_periods.status_code == 404, hidden_periods.text
    assert hidden_facts.status_code == 404, hidden_facts.text


# --- 3. the role ladder on the 14 ---------------------------------------------
@pytest.mark.parametrize("role", ["viewer", "examiner"])
@pytest.mark.parametrize(("method", "path"), FIXED_MUTATION_ROUTES)
def test_read_only_roles_cannot_mutate_the_audited_routes(
    db_client: TestClient, role: str, method: str, path: str
) -> None:
    response = _call(db_client, method, _fill(path), headers(roles=(role,)))
    assert response.status_code in (403, 404), f"{role} {method} {path} -> {response.text}"


def test_analyst_can_still_drive_the_case_and_document_workflow(
    db_client: TestClient, fake_storage, case_api_authority: None
) -> None:
    """No functional regression: every route this change re-guarded still works
    end to end for an explicitly RISK-bound analyst, regardless of token roles."""
    analyst = headers(roles=("analyst",))

    created = db_client.post(
        "/api/v1/cases",
        headers=analyst,
        json={"title": "Boundary regression case", "case_type": "vendor"},
    )
    assert created.status_code == 201, created.text
    case_id = created.json()["id"]

    patched = db_client.patch(
        f"/api/v1/cases/{case_id}", headers=analyst, json={"title": "Renamed"}
    )
    assert patched.status_code == 200, patched.text

    assigned = db_client.post(
        f"/api/v1/cases/{case_id}/assign",
        headers=analyst,
        json={"assigned_to_user_id": str(USER_1)},
    )
    assert assigned.status_code == 200, assigned.text

    assessment = db_client.post(
        "/api/v1/assessments",
        headers=analyst,
        json={
            "case_id": case_id,
            "assessment_type": "vendor_risk",
            "name": "Boundary regression assessment",
        },
    )
    assert assessment.status_code == 201, assessment.text
    run = db_client.post(f"/api/v1/assessments/{assessment.json()['id']}/run", headers=analyst)
    assert run.status_code == 200, run.text

    upload = db_client.post(
        "/api/v1/documents/upload-request",
        headers=analyst,
        json={
            "case_id": case_id,
            "filename": "financials.pdf",
            "content_type": "application/pdf",
            "byte_size": 1234,
        },
    )
    assert upload.status_code == 200, upload.text
    document_id = upload.json()["document_id"]
    fake_storage.head = StoredObjectHead(
        content_type="application/pdf", byte_size=1234, etag='"etag"'
    )
    completed = db_client.post(f"/api/v1/documents/{document_id}/complete-upload", headers=analyst)
    assert completed.status_code == 200, completed.text
    parsed = db_client.post(f"/api/v1/documents/{document_id}/parse", headers=analyst)
    assert parsed.status_code == 200, parsed.text

    validated = db_client.post(f"/api/v1/cases/{case_id}/financial-data/validate", headers=analyst)
    assert validated.status_code == 200, validated.text

    mapped = db_client.post(
        f"/api/v1/cases/{case_id}/financial-workspace/map",
        headers=analyst,
        json={"document_id": document_id, "mappings": []},
    )
    assert mapped.status_code not in (401, 403), mapped.text

    deleted = db_client.delete(f"/api/v1/documents/{document_id}", headers=analyst)
    assert deleted.status_code == 200, deleted.text

    bulk = db_client.post(
        "/api/v1/cases/bulk-actions",
        headers=analyst,
        json={"case_ids": [case_id], "action": "archive"},
    )
    assert bulk.status_code == 200, bulk.text
    assert [item["case_id"] for item in bulk.json()["succeeded"]] == [case_id]


def test_analyst_can_record_a_case_decision(
    db_client: TestClient, case_api_authority: None
) -> None:
    """``POST /cases/{id}/decisions`` preserves scoped Analyst preparation authority.

    The endpoint is mixed: ``needs_more_info`` and ``escalated`` are triage acts
    an analyst must be able to perform. Promoting the whole route to the approver
    ladder would take case triage away from analysts, so the
    four-eyes split on the terminal approve/reject belongs in a separate,
    approver-gated route — a product change, not this authorization fix.
    """
    case_id = str(CaseFactory(db_client).create().id)
    response = db_client.post(
        f"/api/v1/cases/{case_id}/decisions",
        headers=headers(roles=("analyst",)),
        json={"decision": "needs_more_info", "reason": "Awaiting audited accounts."},
    )
    assert response.status_code == 200, response.text
    assert response.json()["decision"] == "needs_more_info"


def test_filing_chain_decision_is_scoped_approve_gated(db_client: TestClient) -> None:
    """Deciding a filing chain needs the exact REG/restricted APPROVE binding,
    not a scalar ``approver`` role.

    The decision route no longer declares a scalar approval ladder: the mutation
    boundary here is the service's scoped gate, so a token that carries the
    ``approver`` role but no binding is refused with the shared denial, and the
    exact binding admits the caller to the (here unknown) object.
    """
    _materialize_sample_bank()
    path = f"/api/v1/banks/{SAMPLE_BANK_ID}/filing-workflow-templates/{uuid4()}/decision"
    payload = {"decision": "approved", "reason": "Exercise the scoped approval boundary."}
    refused = db_client.post(path, headers=headers(roles=("approver",)), json=payload)
    assert refused.status_code == 403, refused.text

    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        authorization.create_role_binding(
            session,
            organization_id=ORG_1,
            principal_user_id=USER_1,
            principal_type=PrincipalType.HUMAN,
            role_bundle=RoleBundle.APPROVER,
            scope=authorization.BindingScope(
                InstitutionScope.INSTITUTION,
                SAMPLE_BANK_ID,
                ModuleScope.REGULATORY,
                SensitivityScope.RESTRICTED,
            ),
            grantor=authorization.GrantorRef(GrantorType.SYSTEM, "test-suite"),
            reason="exercise the filing decision scoped gate",
        )
        user = session.get(User, USER_1)
        assert user is not None
        session.refresh(user)
        version = user.authorization_version
    finally:
        session.close()
    assert version is not None
    admitted = db_client.post(
        path, headers=headers(authorization_version=version), json=payload
    )
    assert admitted.status_code == 404, admitted.text


# --- 4. P0-3: the document delete ---------------------------------------------
def test_document_delete_is_analyst_gated_and_queues_object_cleanup(
    db_client: TestClient, fake_storage, case_api_authority: None
) -> None:
    case_id = str(CaseFactory(db_client).create().id)
    documents = DocumentFactory(db_client, fake_storage)
    document_id = str(documents.create_uploaded(case_id=case_id).document_id)
    path = f"/api/v1/documents/{document_id}"

    for hdrs, label in ((_impersonation_headers(), "impersonation"),):
        refused = db_client.delete(path, headers=hdrs)
        assert refused.status_code == 403, f"{label}: {refused.text}"

    # …and the object is still there: a refused delete deleted nothing.
    still_there = db_client.get(path, headers=headers(roles=("viewer",)))
    assert still_there.status_code == 200
    assert still_there.json()["status"] != "deleted"

    fake_storage.deleted = []
    deleted = db_client.delete(path, headers=headers(roles=("analyst",)))
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["status"] == "deleted"
    assert fake_storage.deleted == []


def test_document_delete_does_not_wait_for_storage(
    db_client: TestClient, fake_storage, case_api_authority: None
) -> None:
    """An unavailable store cannot prevent the durable logical deletion."""
    case_id = str(CaseFactory(db_client).create().id)
    documents = DocumentFactory(db_client, fake_storage)
    document_id = str(documents.create_uploaded(case_id=case_id).document_id)

    def _boom(*, bucket: str, object_key: str) -> None:
        raise RuntimeError("object store unavailable")

    fake_storage.delete_object = _boom  # type: ignore[method-assign]
    try:
        response = db_client.delete(
            f"/api/v1/documents/{document_id}", headers=headers(roles=("analyst",))
        )
    finally:
        del fake_storage.delete_object
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "deleted"
    download = db_client.get(f"/api/v1/documents/{document_id}/download-url", headers=headers())
    assert download.status_code == 409


# --- 5. regression: the treasury / regulatory plane ----------------------------
def test_bank_regulatory_plane_is_unchanged_by_the_boundary_guard(
    db_client: TestClient,
) -> None:
    """The audit's own finding was that these routes are correctly guarded — this
    proves the boundary guard neither loosened nor broke them."""
    official_run = {
        # Capital remains on the pre-cutover scalar mutation gate; Liquidity's
        # exact-binding denial and object hiding are covered separately.
        "module": "capital",
        "reporting_period_id": str(uuid4()),
        "scenario_code": "baseline",
    }

    _materialize_sample_bank()
    for role in ("viewer", "examiner"):
        refused = db_client.post(
            f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-runs",
            headers=headers(roles=(role,)),
            json=official_run,
        )
        assert refused.status_code == 403, f"{role}: {refused.text}"

        hidden = db_client.post(
            "/api/v1/banks/BK-NOEXIST9/regulatory-runs",
            headers=headers(roles=(role,)),
            json=official_run,
        )
        assert hidden.status_code == 404, f"{role}: {hidden.text}"
        assert set(hidden.json()) == {"error"}
        assert set(hidden.json()["error"]) == {"code", "message", "request_id"}
        assert hidden.json()["error"]["message"] == "Bank not found."

    impersonated = db_client.post(
        "/api/v1/banks/BK-NOEXIST9/regulatory-runs",
        headers=_impersonation_headers(),
        json=official_run,
    )
    assert impersonated.status_code == 403, impersonated.text

    # An analyst still passes the gate (the 404 is the handler's own).
    admitted = db_client.post(
        "/api/v1/banks/BK-NOEXIST9/regulatory-runs",
        headers=headers(roles=("analyst",)),
        json=official_run,
    )
    assert admitted.status_code not in (401, 403), admitted.text

    # Reads are untouched for every read-only principal.
    read_only_principals = (
        headers(roles=("viewer",)),
        headers(roles=("examiner",)),
        _impersonation_headers(),
    )
    for hdrs in read_only_principals:
        listing = db_client.get("/api/v1/banks", headers=hdrs)
        assert listing.status_code == 200, listing.text


# --- 4. the ICAAP examiner branch ---------------------------------------------
def test_impersonated_examiner_reads_only_frozen_icaap_cycles(
    db_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A supervisor reads what a Board approved, not a bank's working draft.

    The ICAAP workspace before a freeze is text nobody has signed and figures
    nobody has reviewed. Its own dependency admits an impersonated examiner —
    the only place in the product that bypasses the binding evaluator — so the
    limit has to be pinned here: cycles that were frozen, and nothing else.
    """
    get_settings.cache_clear()

    # Imported here rather than at module scope: this suite walks the whole
    # route table, and importing the ICAAP models at import time would register
    # them before the other cases build their app.
    from app.icaap.domain.frameworks import registry  # noqa: PLC0415
    from app.icaap.models.workspace import IcaapCycle  # noqa: PLC0415
    from app.models import RegulatoryPackage  # noqa: PLC0415
    from tests.fixtures.canonical_bank_fixture import (  # noqa: PLC0415
        SAMPLE_BANK_ID,
        materialize_canonical_test_book,
    )

    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        materialize_canonical_test_book(session)
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
            reason="Create a draft ICAAP for the examiner boundary test.",
        )
        session.commit()
        user = session.get(User, USER_1)
        assert user is not None
        session.refresh(user)
        analyst = headers(roles=("analyst",), authorization_version=user.authorization_version)
    finally:
        session.close()

    base = f"/api/v1/banks/{SAMPLE_BANK_ID}/icaap"
    created = db_client.post(
        f"{base}/cycles",
        json={
            "fiscal_year": 2025,
            "cycle_kind": "rehearsal",
            "basis": "solo",
            "framework_code": "bog_icaap",
            "framework_version": "2026.02-ed.1",
            "reason": "Dry run",
        },
        headers=analyst,
    )
    assert created.status_code == 201, created.text
    cycle_id = created.json()["id"]

    examiner = _impersonation_headers()
    listing = db_client.get(f"{base}/cycles", headers=examiner)
    assert listing.status_code == 200
    assert listing.json()["cycles"] == [], "a draft ICAAP is not a supervisor's to read"

    for path in (
        f"{base}/cycles/{cycle_id}",
        f"{base}/cycles/{cycle_id}/sections",
        f"{base}/cycles/{cycle_id}/sections/executive_summary",
        f"{base}/cycles/{cycle_id}/readiness",
        f"{base}/cycles/{cycle_id}/blocks",
        f"{base}/cycles/{cycle_id}/attachments",
    ):
        response = db_client.get(path, headers=examiner)
        assert response.status_code == 404, f"{path}: {response.text}"

    # A frozen cycle IS a supervisor's to read. A rehearsal can never be sealed
    # (it is a dry run), so the frozen row is written directly here, with the
    # package row its foreign key requires.
    frozen_id = uuid4()
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        package = RegulatoryPackage(
            organization_id=ORG_1,
            bank_id=SAMPLE_BANK_ID,
            return_family="icaap_stress",
            return_code="ICAAP-STRESS",
            reporting_date=dt.date(2025, 12, 31),
            frequency="annual",
            basis="solo",
            status="generated",
            version=1,
            snapshot={},
            generated_by=USER_1,
        )
        session.add(package)
        session.flush()
        session.add(
            IcaapCycle(
                id=frozen_id,
                organization_id=ORG_1,
                bank_id=SAMPLE_BANK_ID,
                fiscal_year=2025,
                as_of_date=dt.date(2025, 12, 31),
                cycle_kind="annual",
                basis="solo",
                title="ICAAP FY2025 (Solo)",
                framework_code="bog_icaap",
                framework_version="2026.02-ed.1",
                framework_sha256=registry.get("bog_icaap", "2026.02-ed.1").digest,
                status="frozen",
                due_date=dt.date(2026, 3, 31),
                due_date_basis="framework",
                package_id=package.id,
                frozen_at=utc_now(),
                created_by=USER_1,
            )
        )
        session.commit()
    finally:
        session.close()

    listed = db_client.get(f"{base}/cycles", headers=examiner)
    assert listed.status_code == 200
    assert [entry["id"] for entry in listed.json()["cycles"]] == [str(frozen_id)]
    detail = db_client.get(f"{base}/cycles/{frozen_id}", headers=examiner)
    assert detail.status_code == 200
    # ...and the draft alongside it is still invisible.
    assert db_client.get(f"{base}/cycles/{cycle_id}", headers=examiner).status_code == 404


def test_an_impersonated_examiner_can_never_write_to_the_icaap_workspace(
    db_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    get_settings.cache_clear()
    base = "/api/v1/banks/BK-SAMP0001/icaap"
    cycle = uuid4()
    unsafe = (
        ("POST", f"{base}/cycles"),
        ("PATCH", f"{base}/cycles/{cycle}"),
        ("POST", f"{base}/cycles/{cycle}/archive"),
        ("POST", f"{base}/cycles/{cycle}/rebase"),
        ("PUT", f"{base}/cycles/{cycle}/sections/executive_summary/working"),
        ("POST", f"{base}/cycles/{cycle}/sections/executive_summary/versions"),
        ("POST", f"{base}/cycles/{cycle}/blocks"),
    )
    for method, path in unsafe:
        response = _call(db_client, method, path, _impersonation_headers())
        assert response.status_code == 403, f"{method} {path}: {response.text}"
