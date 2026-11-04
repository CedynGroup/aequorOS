"""Structural guard: every operator route that names a TENANT is gated + audited.

The staff plane's core invariant is that staff access to a bank's data passes
the Tenant Inspector gate (``app.operator.inspection.require_active_inspection``)
and lands in append-only ``operator_audit_log`` with ``target_org`` naming the
tenant. Until 2026-08-23 the desk entitlement routes did neither: they took the
organization from the client, gated on the base ``Operator`` dependency (which
admits the lowest explicitly provisioned ``developer`` tier), and the read left
no audit row whatsoever (audit finding D-26). Workforce OIDC now requires an
active ``operator_users`` row, so domain membership alone cannot reach even
that base dependency.

Patching those four routes would have fixed those four routes. This module
fixes the CLASS instead: it DERIVES the set of tenant-naming operator routes
from the live app — a route names a tenant when it takes an ``org_id`` /
``organization_id`` path parameter, query parameter, or request-body field —
and asserts each one is gated and audited, with a short, reasoned exemption
list. A new route that can name a tenant is therefore in the set the moment it
is mounted, and fails here until it is gated, or until somebody writes down why
it should not be.

A second, wider derivation covers the invariant the gate one cannot see: EVERY
operator mutation — tenant-scoped, fleet-wide or desk-global — lands in
append-only ``operator_audit_log``. The desk and control-plane writes name no
tenant (a methodology approval, a curve publish, an operating-environment
score) yet reach every tenant at once through ``pull_runner.execute_pull``, so
"names an organization" is the wrong boundary for the audit half of the rule.

Deliberately source-level: it reads each endpoint's own body rather than
exercising it, so it covers routes no behavioural test happens to reach. The
behavioural half — that the gate actually returns 403 and the audit row
actually appears — lives in ``test_desk_entitlements_api.py`` and
``test_inspector_fix_api.py``.
"""

from __future__ import annotations

import inspect
from types import ModuleType

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

_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

#: Reads of the staff plane's own records, not of a tenant. They filter on a
#: tenant name without reading tenant data, so neither the session gate nor an
#: audit row applies. Each also carries its reason in UNGATED_BY_DESIGN.
_STAFF_PLANE_READS = frozenset(
    {("GET", "/operator/v1/audit"), ("GET", "/operator/v1/inspector/sessions")}
)


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


def test_every_tenant_naming_route_passes_the_inspector_gate() -> None:
    offenders: list[str] = []
    for method, path, route in _tenant_naming_routes():
        if (method, path) in UNGATED_BY_DESIGN:
            continue
        source = inspect.getsource(route.endpoint)
        if "require_active_inspection(" not in source:
            offenders.append(f"{method} {path} ({route.endpoint.__qualname__})")
    assert offenders == [], (
        "operator routes name a tenant but never call require_active_inspection — "
        "gate them, or add a reasoned entry to UNGATED_BY_DESIGN: " + str(offenders)
    )


def test_every_tenant_naming_route_writes_an_audit_row_naming_the_tenant() -> None:
    offenders: list[str] = []
    for method, path, route in _tenant_naming_routes():
        source = inspect.getsource(route.endpoint)
        if (method, path) in _STAFF_PLANE_READS:
            # Not tenant-scoped access at all: these read the staff plane's own
            # records (who looked at which tenant, and what staff did). Auditing
            # a read of the audit log floods the log it reads. Reasons are
            # recorded in UNGATED_BY_DESIGN.
            continue
        if "record_operator_action(" not in source or "target_org=" not in source:
            offenders.append(f"{method} {path} ({route.endpoint.__qualname__})")
    assert offenders == [], (
        "operator routes touch one tenant without an operator_audit_log row "
        "carrying target_org — every operator action against a tenant must be "
        "recorded: " + str(offenders)
    )


def test_tenant_mutations_that_cannot_be_inspected_are_still_audited() -> None:
    """A mutation with no tenant to inspect is not a mutation with no record."""
    routes = {(method, path): route for method, path, route in _operator_routes()}
    for key, reason in AUDITED_WITHOUT_A_TENANT_TO_INSPECT.items():
        route = routes.get(key)
        assert route is not None, f"exemption names a route that no longer exists: {key}"
        assert key[0] in _MUTATING_METHODS, f"{key} is not a mutation: {reason}"
        # Provisioning delegates to its saga, so follow one level down.
        module = inspect.getmodule(route.endpoint)
        assert module is not None, f"cannot locate the module defining {key}"
        source = inspect.getsource(module)
        assert "record_operator_action(" in source or "tenant_provisioning" in source, key


def test_no_exemption_outlives_its_route() -> None:
    """A stale exemption is a hole nobody is looking at any more."""
    live = {(method, path) for method, path, _ in _operator_routes()}
    stale = sorted(set(UNGATED_BY_DESIGN) - live) + sorted(
        set(AUDITED_WITHOUT_A_TENANT_TO_INSPECT) - live
    )
    assert stale == [], f"exemptions for routes that no longer exist: {stale}"


# -- the wider rule: every operator MUTATION is recorded ---------------------------
#
# The gate above asks "does this route name a tenant?". That is the right
# question for the inspection session and the wrong one for the audit trail: a
# desk methodology approval, a curve publish and an operating-environment score
# name no organization and reach EVERY tenant at once. Deriving from the
# mutating verbs instead makes the audit rule cover the whole staff plane.
UNAUDITED_BY_DESIGN: dict[tuple[str, str], str] = {
    ("POST", "/operator/auth/login"): (
        "The front door, and the only unauthenticated operator route. There is "
        "no operator principal yet, and operator_audit_log rows are keyed by "
        "one — a failed sign-in has no author to name. The staff plane records "
        "it in the two places that can: operator_users.last_login_at on success, "
        "and an auth_anomaly emission carrying operator_user_id (never the "
        "email) when repeated failures lock the account "
        "(app/operator/services/operator_auth.py::record_account_failure)."
    ),
}


def _audits_via_a_service(route: APIRoute) -> bool:
    """True when the endpoint hands the whole operation to a service that
    audits it. Derived, not hardcoded: the endpoint's own module namespace is
    walked for imported ``app.*`` modules that call ``record_operator_action``.

    Only the routes in :data:`AUDITED_WITHOUT_A_TENANT_TO_INSPECT` are allowed
    to satisfy the rule this way — tenant provisioning is a saga that audits
    every step, including the failures, in a fresh transaction after rollback,
    so the call cannot live in the endpoint body.
    """
    module = inspect.getmodule(route.endpoint)
    if module is None:
        return False
    for value in vars(module).values():
        if not isinstance(value, ModuleType) or not value.__name__.startswith("app."):
            continue
        try:
            source = inspect.getsource(value)
        except OSError:  # pragma: no cover - a namespace package has no source
            continue
        if "record_operator_action(" in source:
            return True
    return False


def _mutating_routes() -> list[tuple[str, str, APIRoute]]:
    return [row for row in _operator_routes() if row[0] in _MUTATING_METHODS]


def test_every_operator_mutation_lands_in_the_audit_log() -> None:
    """No staff-plane write is invisible, whether or not it names a tenant."""
    mutations = _mutating_routes()
    assert len(mutations) >= 40, f"suspiciously few operator mutations: {len(mutations)}"
    offenders: list[str] = []
    for method, path, route in mutations:
        if (method, path) in UNAUDITED_BY_DESIGN:
            continue
        source = inspect.getsource(route.endpoint)
        if (method, path) in AUDITED_WITHOUT_A_TENANT_TO_INSPECT and _audits_via_a_service(route):
            continue
        if "record_operator_action(" not in source:
            offenders.append(f"{method} {path} ({route.endpoint.__qualname__})")
    assert offenders == [], (
        "operator routes mutate state without writing an operator_audit_log row — "
        "every staff action must be attributable to the operator who took it. "
        "Audit them, or add a reasoned entry to UNAUDITED_BY_DESIGN: " + str(offenders)
    )


def test_no_audit_exemption_outlives_its_route() -> None:
    live = {(method, path) for method, path, _ in _operator_routes()}
    stale = sorted(set(UNAUDITED_BY_DESIGN) - live)
    assert stale == [], f"audit exemptions for routes that no longer exist: {stale}"
