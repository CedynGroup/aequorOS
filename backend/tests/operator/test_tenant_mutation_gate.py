"""Live-route inventory for tenant parameters and documented exemptions."""

from __future__ import annotations

from fastapi.routing import APIRoute

from app.operator.main import create_operator_app

#: Path- / query- / body-parameter names that name one tenant. ``target_org``
#: is here because leaving it out was a SILENT miss rather than a decision:
#: ``GET /operator/v1/audit`` filters on it, so without it that route sat
#: outside the derivation with nothing recording why. It is now in the set and
#: carries a reason, which is the whole point of a structural guard.
ORG_PARAM_NAMES = frozenset({"org_id", "organization_id", "target_org"})

#: Routes that name a tenant and are deliberately NOT inspection-gated. Each
#: entry is a reason, not a waiver — an exemption without one does not exist.
UNGATED_BY_DESIGN: dict[tuple[str, str], str] = {
    ("GET", "/operator/v1/tenants/{org_id}"): (
        "Fleet metadata, not a look inside: the same health row the board "
        "returns, so the console can refresh one tenant without refetching the "
        "list. Documented as open in app/operator/inspection.py — and it is "
        "still audited as tenants.get with target_org."
    ),
    ("POST", "/operator/v1/inspector/sessions"): (
        "This route IS the gate. Requiring an active session to open a session "
        "would make the control unreachable. Audited as inspector.session.start."
    ),
    ("GET", "/operator/v1/audit"): (
        "The staff plane's OWN cross-tenant action log, gated to operator_admin "
        "and above. target_org here is a filter over operator records, not a "
        "tenant read: gating it on a session for one organization would make "
        "the cross-tenant view — the reason an auditor opens the page — "
        "unreachable. Reading it is also deliberately not itself audited, "
        "because a page view would flood the log it reads "
        "(app/operator/features/audit_log.py)."
    ),
    ("GET", "/operator/v1/inspector/sessions"): (
        "The inspection audit surface itself — it lists operator-plane session "
        "records (who looked at which tenant, when), never tenant data. "
        "organization_id here is a filter over staff records, so gating it on a "
        "session would hide the very history an auditor opens the page to read."
    ),
}

#: Mutating routes that create or act across tenants and so cannot name one to
#: inspect, but MUST still be audited. Checked for audit only.
AUDITED_WITHOUT_A_TENANT_TO_INSPECT: dict[tuple[str, str], str] = {
    ("POST", "/operator/v1/tenants"): (
        "Tenant provisioning: the organization does not exist yet, so there is "
        "nothing to open a session against. The saga audits every attempt "
        "(tenants.provision, target_org set once the org id exists) including "
        "the failures, in a fresh transaction after rollback."
    ),
}


def _org_params(route: APIRoute) -> set[str]:
    """The tenant-naming parameters this route accepts, from the live app."""
    dependant = route.dependant
    names = {p.name for p in (dependant.path_params + dependant.query_params)}
    body_field = route.body_field
    if body_field is not None:
        model_fields = getattr(body_field.field_info.annotation, "model_fields", None)
        if model_fields:
            names |= set(model_fields)
    return names & ORG_PARAM_NAMES


def _operator_routes() -> list[tuple[str, str, APIRoute]]:
    """(method, path, route) for every operator API route, one row per method."""
    rows: list[tuple[str, str, APIRoute]] = []
    for route in create_operator_app().routes:
        if not isinstance(route, APIRoute):
            continue
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            rows.append((method, route.path, route))
    return rows


def _tenant_naming_routes() -> list[tuple[str, str, APIRoute]]:
    return [row for row in _operator_routes() if _org_params(row[2])]


def test_the_derivation_actually_finds_routes() -> None:
    """Guard the guard: an empty set would make every assertion below pass
    vacuously, which is exactly how a structural test rots into decoration."""
    found = _tenant_naming_routes()
    paths = {(method, path) for method, path, _ in found}
    assert len(found) >= 20, f"suspiciously few tenant-naming routes: {sorted(paths)}"
    # A representative of each shape the derivation must catch: a path
    # parameter, a request-body field, and a query parameter.
    assert ("POST", "/operator/v1/tenants/{org_id}/fix/config") in paths
    assert ("POST", "/operator/v1/desk/entitlements/grant-tier") in paths
    assert ("GET", "/operator/v1/desk/entitlements") in paths


def test_no_exemption_outlives_its_route() -> None:
    """A stale exemption is a hole nobody is looking at any more."""
    live = {(method, path) for method, path, _ in _operator_routes()}
    stale = sorted(set(UNGATED_BY_DESIGN) - live) + sorted(
        set(AUDITED_WITHOUT_A_TENANT_TO_INSPECT) - live
    )
    assert stale == [], f"exemptions for routes that no longer exist: {stale}"


UNAUDITED_BY_DESIGN: dict[tuple[str, str], str] = {
    ("POST", "/operator/auth/login"): (
        "The ordinary password sign-in front door. There is "
        "no operator principal yet, and operator_audit_log rows are keyed by "
        "one — a failed sign-in has no author to name. The staff plane records "
        "it in the two places that can: operator_users.last_login_at on success, "
        "and an auth_anomaly emission carrying operator_user_id (never the "
        "email) when repeated failures lock the account "
        "(app/operator/services/operator_auth.py::record_account_failure)."
    ),
}


def test_no_audit_exemption_outlives_its_route() -> None:
    live = {(method, path) for method, path, _ in _operator_routes()}
    stale = sorted(set(UNAUDITED_BY_DESIGN) - live)
    assert stale == [], f"audit exemptions for routes that no longer exist: {stale}"
