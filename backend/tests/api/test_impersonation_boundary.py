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
   structural — it is carried by the route's own ``MutationTenant`` /
   ``ApproverTenant`` declaration — so the route-table sweep below is what keeps
   it honest: it is the test that would have caught P0-2.

The earlier suite (``test_impersonation_auth.py``) sampled five paths that all
already used ``MutationTenant``, so it could not see the defect. This one walks
the whole table.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, cast
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.api.deps import IMPERSONATION_READ_ONLY_ROUTES, MUTATION_ROLE_DEPENDENCY_NAMES
from app.core import security
from app.core.config import get_settings
from app.db.base import utc_now
from app.integrations.storage.base import StoredObjectHead
from tests.api.factories import CaseFactory, DocumentFactory
from tests.api.helpers import ORG_1, USER_1, headers

_UNSAFE_METHODS = ("POST", "PUT", "PATCH", "DELETE")

# Every unsafe-method route that still resolves only the read-only ``Tenant``
# context, with the reason each is tolerated. The sweep asserts the read-guarded
# set is a SUBSET of this list, so a concurrent owner tightening one of them
# shrinks the set and keeps the test green, while any NEW read-guarded mutation
# fails it.
#
# Note the audit's own count of 14 was taken WITHOUT crediting the explicit
# explicit admin dependencies on the attestation, integration-key and
# SSO-administration routes; those were never viewer-writable. With those gates
# credited (see ``_is_role_guarded``), these three are the whole residue.
_UNFIXED_READ_GUARDED_UNSAFE_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        # Legitimately viewer-writable: a user editing their OWN profile. Raising
        # it to the analyst floor would stop a viewer changing their own name.
        ("PATCH", "/api/v1/auth/me"),
        # Genuinely read-only compute; see IMPERSONATION_READ_ONLY_ROUTES.
        ("POST", "/api/v1/banks/{bank_id}/scenario-workbench/{module}/analysis"),
    }
)

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


def _is_role_guarded(names: set[str]) -> bool:
    """True if the route requires a write role — via ``MutationTenant`` /
    ``ApproverTenant``, or via an explicit ``require_role`` of analyst or above."""
    return bool(
        names & {"get_mutation_tenant_context", "get_approver_tenant_context"}
        or names & MUTATION_ROLE_DEPENDENCY_NAMES
    )


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


def _call(client: TestClient, method: str, path: str, hdrs: dict[str, str]) -> Any:
    return client.request(method, path, headers=hdrs, json={})


# --- 1. the structural sweep --------------------------------------------------
def test_no_new_mutation_hides_behind_the_read_guard(client: TestClient) -> None:
    """The test that would have caught P0-2: walk the whole route table.

    Every unsafe-method route must either carry a role gate, or be one of the
    known-unfixed routes owned by another stream, or have no tenant context at
    all (login/refresh/SSO, and the two 422 rejectors that take no dependency).
    """
    offenders: list[tuple[str, str]] = []
    for method, path, names in _unsafe_routes(client):
        if _is_role_guarded(names):
            continue
        if "get_current_principal" not in names:
            continue  # unauthenticated by design — no tenant context to guard
        if (method, path) in _UNFIXED_READ_GUARDED_UNSAFE_ROUTES:
            continue
        offenders.append((method, path))
    assert offenders == [], (
        "these mutations are behind the read-only Tenant guard, so a viewer can "
        f"perform them: {offenders}"
    )


def test_the_fourteen_audited_routes_now_carry_a_role_gate(client: TestClient) -> None:
    guards = {
        (method, path): _is_role_guarded(names) for method, path, names in _unsafe_routes(client)
    }
    for entry in FIXED_MUTATION_ROUTES:
        assert entry in guards, f"{entry} is no longer in the route table"
        assert guards[entry], f"{entry} is still behind the read-only Tenant guard"


def test_account_admin_cannot_use_approver_gated_regulatory_submission(
    db_client: TestClient,
) -> None:
    """The legacy admin split must remove operational superuser authority.

    The resource need not exist: the role dependency runs before the workflow
    lookup, proving an account-only administrator cannot reach the submission
    service through the real regulatory route.
    """

    response = db_client.post(
        f"/api/v1/banks/BK-SAMP0001/regulatory-packages/{uuid4()}/submit",
        headers=headers(roles=("account_admin",)),
        json={"channel": "email"},
    )
    assert response.status_code == 403
    assert "analyst" in response.json()["error"]["message"]


def test_account_admin_is_limited_to_account_administration(db_client: TestClient) -> None:
    account_headers = headers(roles=("account_admin",))

    integration_keys = db_client.get("/api/v1/integration-keys", headers=account_headers)
    assert integration_keys.status_code == 200, integration_keys.text

    attestation_templates = db_client.put(
        "/api/v1/attestation/signature-placements",
        headers=account_headers,
        json={},
    )
    assert attestation_templates.status_code == 403
    assert "admin" in attestation_templates.json()["error"]["message"]


def test_impersonation_exemption_set_is_minimal_and_real(client: TestClient) -> None:
    """Every exemption must name a route that exists and is an unsafe method."""
    table = {(method, path) for method, path, _ in _unsafe_routes(client)}
    assert table >= IMPERSONATION_READ_ONLY_ROUTES, (
        "stale exemption entries: "
        f"{sorted(IMPERSONATION_READ_ONLY_ROUTES - table)}"  # pragma: no cover
    )
    # Deliberately pinned: widening this hole is a reviewable decision, not a
    # drive-by edit. ``analysis_workbench.run_analysis`` persists nothing.
    assert (
        frozenset({("POST", "/api/v1/banks/{bank_id}/scenario-workbench/{module}/analysis")})
        == IMPERSONATION_READ_ONLY_ROUTES
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


def test_impersonated_examiner_can_still_read(db_client: TestClient) -> None:
    """Regression: the boundary guard touches only unsafe methods."""
    response = db_client.get("/api/v1/banks", headers=_impersonation_headers())
    assert response.status_code == 200, response.text


# --- 3. the role ladder on the 14 ---------------------------------------------
@pytest.mark.parametrize("role", ["viewer", "examiner"])
@pytest.mark.parametrize(("method", "path"), FIXED_MUTATION_ROUTES)
def test_read_only_roles_cannot_mutate_the_audited_routes(
    db_client: TestClient, role: str, method: str, path: str
) -> None:
    response = _call(db_client, method, _fill(path), headers(roles=(role,)))
    assert response.status_code == 403, f"{role} {method} {path} -> {response.text}"


def test_analyst_can_still_drive_the_case_and_document_workflow(
    db_client: TestClient, fake_storage
) -> None:
    """No functional regression: every route this change re-guarded still works
    end to end for an ``analyst`` — the lowest role that was ever entitled."""
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


def test_analyst_can_record_a_case_decision(db_client: TestClient) -> None:
    """``POST /cases/{id}/decisions`` stays on the analyst floor.

    The endpoint is mixed: ``needs_more_info`` and ``escalated`` are triage acts
    an analyst must be able to perform. Promoting the whole route to
    ``ApproverTenant`` would take case triage away from analysts, so the
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


def test_approver_ladder_still_gates_an_approval_action(db_client: TestClient) -> None:
    """``ApproverTenant`` remains distinct from the analyst floor."""
    path = "/api/v1/banks/BK-NOEXIST9/capital-plan/approve"
    refused = db_client.post(path, headers=headers(roles=("analyst",)), json={})
    assert refused.status_code == 403, refused.text

    admitted = db_client.post(path, headers=headers(roles=("approver",)), json={})
    assert admitted.status_code not in (401, 403), admitted.text


# --- 4. P0-3: the document delete ---------------------------------------------
def test_document_delete_is_analyst_gated_and_removes_the_object(
    db_client: TestClient, fake_storage
) -> None:
    case_id = str(CaseFactory(db_client).create().id)
    documents = DocumentFactory(db_client, fake_storage)
    document_id = str(documents.create_uploaded(case_id=case_id).document_id)
    path = f"/api/v1/documents/{document_id}"

    for hdrs, label in (
        (headers(roles=("viewer",)), "viewer"),
        (headers(roles=("examiner",)), "examiner"),
        (_impersonation_headers(), "impersonation"),
    ):
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
    assert len(fake_storage.deleted) == 1


def test_storage_delete_failure_does_not_commit_a_false_success(
    db_client: TestClient, fake_storage
) -> None:
    """P0-3 consistency: the irreversible act runs BEFORE the commit.

    ``documents.delete_document`` marks the rows deleted in the session, calls
    ``storage_client.delete_object``, and only then commits. When the object
    store refuses, the exception propagates through ``get_tenant_db_session``,
    which rolls the session back — so the caller sees a failure and the document
    is still readable and still marked live. Reversing that order would trade
    this recoverable failure for silent data loss on a commit error.
    """
    case_id = str(CaseFactory(db_client).create().id)
    documents = DocumentFactory(db_client, fake_storage)
    document_id = str(documents.create_uploaded(case_id=case_id).document_id)

    def _boom(*, bucket: str, object_key: str) -> None:
        raise RuntimeError(f"object store unavailable: {bucket}/{object_key}")

    fake_storage.delete_object = _boom  # type: ignore[method-assign]
    try:
        response = db_client.delete(
            f"/api/v1/documents/{document_id}", headers=headers(roles=("analyst",))
        )
    finally:
        del fake_storage.delete_object

    assert response.status_code >= 500, response.text

    survivor = db_client.get(f"/api/v1/documents/{document_id}", headers=headers())
    assert survivor.status_code == 200, survivor.text
    assert survivor.json()["status"] != "deleted", "the DB committed a delete that never happened"


# --- 5. regression: the treasury / regulatory plane ----------------------------
def test_bank_regulatory_plane_is_unchanged_by_the_boundary_guard(
    db_client: TestClient,
) -> None:
    """The audit's own finding was that these routes are correctly guarded — this
    proves the boundary guard neither loosened nor broke them."""
    official_run = {
        "module": "liquidity",
        "reporting_period_id": str(uuid4()),
        "scenario_code": "baseline",
    }

    for role in ("viewer", "examiner"):
        refused = db_client.post(
            "/api/v1/banks/BK-NOEXIST9/regulatory-runs",
            headers=headers(roles=(role,)),
            json=official_run,
        )
        assert refused.status_code == 403, f"{role}: {refused.text}"

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
