"""Every direct credit route names a scoped-binding dependency, and the right one.

The Phase 4 cutover (``backend/docs/credit_enforcement_rollout.md``) replaced the
credit routes' bare ``Tenant`` / ``MutationTenant`` gates with per-route
``_require_institution_permission`` dependencies on ``Module.CREDIT``. Two
different regressions are pinned here, because the interesting one is the second:

* a listed route silently reverting to a scalar gate, and
* a NEW credit route shipping with no sentence at all — which is what the module
  looked like for its whole life before this cutover, and which no test could
  have caught while the census of credit routes lived only in a document.

So the enumeration is derived from the app, not from the table: every route whose
path contains ``/credit/`` must appear in ``_ROUTE_DEPENDENCIES``, and every entry
must match exactly one route.
"""

from __future__ import annotations

import inspect

import pytest
from fastapi.routing import APIRoute

from app.api import deps
from app.api.deps import MUTATION_ROLE_DEPENDENCY_NAMES
from app.main import create_app

_CREDIT_PREFIX = "/api/v1/banks/{bank_id}/credit/"

#: The authority table of the rollout contract, executable. Keep the two in step.
_ROUTE_DEPENDENCIES: dict[tuple[str, str], str] = {
    ("POST", f"{_CREDIT_PREFIX}run-all-scenarios"): "require_credit_run",
    ("GET", f"{_CREDIT_PREFIX}dashboard"): "require_credit_aggregated_view",
    ("GET", f"{_CREDIT_PREFIX}loans"): "require_credit_blotter_view",
    ("GET", f"{_CREDIT_PREFIX}loans/facets"): "require_credit_blotter_view",
    ("GET", f"{_CREDIT_PREFIX}concentration"): "require_credit_concentration_view",
    ("GET", f"{_CREDIT_PREFIX}activity"): "require_credit_activity_view",
    ("GET", f"{_CREDIT_PREFIX}migration"): "require_credit_aggregated_view",
    ("GET", f"{_CREDIT_PREFIX}vintages"): "require_credit_aggregated_view",
    ("GET", f"{_CREDIT_PREFIX}pd"): "require_credit_aggregated_view",
}


def _dependency_names(route: APIRoute) -> set[str]:
    names: set[str] = set()
    stack = [route.dependant]
    while stack:
        dependant = stack.pop()
        if dependant.call is not None:
            names.add(getattr(dependant.call, "__name__", ""))
        stack.extend(dependant.dependencies)
    return names


def _module_entitlement_keys(route: APIRoute) -> set[str]:
    """The module keys this route's ``require_module_access`` gates on.

    That factory returns an anonymous closure, so the dependency cannot be
    identified by name; the ``module_key`` it closed over is read instead, which
    is the value the gate actually compares.
    """
    keys: set[str] = set()
    stack = [route.dependant]
    while stack:
        dependant = stack.pop()
        call = dependant.call
        closure = getattr(call, "__closure__", None) or ()
        variables = getattr(getattr(call, "__code__", None), "co_freevars", ())
        for name, cell in zip(variables, closure, strict=False):
            if name == "module_key" and isinstance(cell.cell_contents, str):
                keys.add(cell.cell_contents)
        stack.extend(dependant.dependencies)
    return keys


def _credit_routes() -> list[APIRoute]:
    return [
        route
        for route in create_app().routes
        if isinstance(route, APIRoute) and _CREDIT_PREFIX in route.path
    ]


def test_every_credit_route_is_in_the_authority_table() -> None:
    """A new credit route cannot ship without a recorded sentence."""
    found = {
        (method, route.path)
        for route in _credit_routes()
        for method in (route.methods or set())
        if method not in {"HEAD", "OPTIONS"}
    }
    assert found == set(_ROUTE_DEPENDENCIES), (
        "credit routes and the authority table disagree; add the route to "
        "_ROUTE_DEPENDENCIES and to backend/docs/credit_enforcement_rollout.md"
    )


@pytest.mark.parametrize(("key", "required"), sorted(_ROUTE_DEPENDENCIES.items()))
def test_credit_routes_cannot_revert_to_legacy_authorization(
    key: tuple[str, str], required: str
) -> None:
    method, path = key
    matches = [
        route
        for route in _credit_routes()
        if route.path == path and method in (route.methods or set())
    ]
    assert len(matches) == 1, f"{method} {path} must have exactly one route"
    dependencies = _dependency_names(matches[0])
    assert required in dependencies
    # The scalar gates the cutover removed.
    assert "get_mutation_tenant_context" not in dependencies
    assert "get_approver_tenant_context" not in dependencies
    assert not any(name.startswith("require_role_") for name in dependencies)
    # Visibility still decides first: a bank of another tenant is 404 before any
    # sentence is evaluated.
    assert "resolve_tenant_bank" in dependencies
    # Entitlement is orthogonal to authority and stays.
    assert "credit" in _module_entitlement_keys(matches[0])


def test_the_shared_institution_gate_refuses_a_narrowed_scope_by_default() -> None:
    """An unconverted surface must REFUSE a branch grant, not ignore it.

    The default on ``_require_institution_permission`` is the whole safety of the
    data-scope dimension for every module that has not applied it: with the other
    default, an Org Owner could compose a sentence restricting a reader to one
    branch and thirteen institution surfaces would serve the whole book anyway.
    Only a surface that APPLIES the scope may opt out, and the opt-outs are
    enumerated here so adding one is a visible decision.
    """
    signature = inspect.signature(deps._require_institution_permission)  # noqa: SLF001
    assert signature.parameters["require_whole_institution"].default is True

    source = inspect.getsource(deps)
    opt_outs = source.count("require_whole_institution=False")
    assert opt_outs == 2, (
        "exactly two dependencies apply a data scope instead of refusing one "
        "(the credit blotter/facets and the credit activity grid); a new opt-out "
        "must apply access.data_scope to its rows AND to every count"
    )


def test_the_credit_run_route_is_a_recognized_mutation_gate() -> None:
    """``run-all-scenarios`` mints regulatory runs, so the impersonation boundary
    must classify it as a guarded mutation — an unrecognized write gate is how an
    act-as-examiner session would reach a POST that persists."""
    assert "require_credit_run" in MUTATION_ROLE_DEPENDENCY_NAMES
