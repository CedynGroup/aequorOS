"""Every direct credit route names a scoped-binding dependency, and the right one.

The Phase 4 cutover (``backend/docs/credit_enforcement_rollout.md``) replaced the
credit routes' bare ``Tenant`` / ``MutationTenant`` gates with per-route
``_require_institution_permission`` dependencies on ``Module.CREDIT``. The
Phase 5 remainder moved the credit-authorized reads that live outside
``/credit/`` — the four Board registers and the two SDI diagnostics that disclose
the loan book — onto the same sentences. Two different regressions are pinned
here, because the interesting one is the second:

* a listed route silently reverting to a scalar gate, and
* a NEW credit route shipping with no sentence at all — which is what the module
  looked like for its whole life before this cutover, and which no test could
  have caught while the census of credit routes lived only in a document.

So the enumeration is derived from the app, not from the table: every route whose
path contains ``/credit/`` must appear in ``_ROUTE_DEPENDENCIES``, every entry
must match exactly one route, and every route in ``_CREDIT_AUTHORIZED_READS``
must exist with the dependency it names.
"""

from __future__ import annotations

import pytest
from fastapi.routing import APIRoute

from app.api.deps import MUTATION_ROLE_DEPENDENCY_NAMES
from app.main import app

_CREDIT_PREFIX = "/api/v1/banks/{bank_id}/credit/"
_BANK_PREFIX = "/api/v1/banks/{bank_id}"

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

#: The four Board registers. Every GET on the register router must appear here, so
#: a fifth register cannot ship on the old tenancy-only gate.
_REGISTER_READS: dict[tuple[str, str], str] = {
    ("GET", f"{_BANK_PREFIX}/ecl-assumptions"): "require_credit_restricted_view",
    ("GET", f"{_BANK_PREFIX}/crm-haircuts"): "require_credit_restricted_view",
    ("GET", f"{_BANK_PREFIX}/concentration-limits"): "require_credit_restricted_view",
    ("GET", f"{_BANK_PREFIX}/credit-thresholds"): "require_credit_restricted_view",
}

#: SDI diagnostics that disclose the credit book, now governed by Credit rather
#: than Capital. ``/sdi/readiness`` is deliberately absent: it reports which data
#: each module still needs, not the loan book.
_SDI_CREDIT_READS: dict[tuple[str, str], str] = {
    ("GET", f"{_BANK_PREFIX}/loan-classification"): "require_credit_aggregated_view",
    ("GET", f"{_BANK_PREFIX}/sdi/loan-classification"): "require_credit_aggregated_view",
    ("GET", f"{_BANK_PREFIX}/sdi/large-exposures"): "require_credit_restricted_view",
}

#: Credit-authorized reads that do not live under ``/credit/``. The four Board
#: registers are the institution's adopted assumptions and limits and can name a
#: counterparty group; the two SDI diagnostics disclose the classified book and
#: the named large exposures. All take CREDIT, and none takes Capital.
_CREDIT_AUTHORIZED_READS: dict[tuple[str, str], str] = {
    **_REGISTER_READS,
    **_SDI_CREDIT_READS,
}

#: The register writes carry issue 184's Configurator authority: the route
#: dependency deliberately checks no scalar role — the product service resolves
#: the target bank and requires the exact stored ``credit``/``restricted``
#: ``configure`` sentence before any row is touched.
_REGISTER_WRITES: dict[tuple[str, str], str] = {
    ("PUT", f"{_BANK_PREFIX}/ecl-assumptions"): "get_scoped_mutation_tenant_context",
    ("PUT", f"{_BANK_PREFIX}/crm-haircuts"): "get_scoped_mutation_tenant_context",
    ("PUT", f"{_BANK_PREFIX}/concentration-limits"): "get_scoped_mutation_tenant_context",
    ("PUT", f"{_BANK_PREFIX}/credit-thresholds"): "get_scoped_mutation_tenant_context",
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
        for route in app.routes
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


def test_the_credit_run_route_is_a_recognized_mutation_gate() -> None:
    """``run-all-scenarios`` mints regulatory runs, so the impersonation boundary
    must classify it as a guarded mutation — an unrecognized write gate is how an
    act-as-examiner session would reach a POST that persists."""
    assert "require_credit_run" in MUTATION_ROLE_DEPENDENCY_NAMES


@pytest.mark.parametrize(("key", "required"), sorted(_CREDIT_AUTHORIZED_READS.items()))
def test_credit_authorized_reads_outside_credit_cut_over(
    key: tuple[str, str], required: str
) -> None:
    """The register and SDI reads carry the same exact sentence as ``/credit/*``.

    They were the last tenancy-only credit reads: any authenticated user of the
    tenant could fetch the Board's ECL assumptions, the named concentration
    limits, the classified book and the counterparty large-exposure list. A
    Capital binding is not one of these sentences.
    """
    method, path = key
    matches = [
        route
        for route in app.routes
        if isinstance(route, APIRoute) and route.path == path and method in (route.methods or set())
    ]
    assert len(matches) == 1, f"{method} {path} must have exactly one route"
    dependencies = _dependency_names(matches[0])
    assert required in dependencies
    assert "resolve_tenant_bank" in dependencies
    assert "get_mutation_tenant_context" not in dependencies
    assert "get_approver_tenant_context" not in dependencies
    assert not any(name.startswith("require_role_") for name in dependencies)
    # Capital authority is a different sentence; its dependency must not appear
    # on a credit read, or the cutover would silently keep admitting Capital.
    assert not any(name.startswith("require_capital_") for name in dependencies)


@pytest.mark.parametrize(("key", "required"), sorted(_REGISTER_WRITES.items()))
def test_register_writes_are_configurator_authority(key: tuple[str, str], required: str) -> None:
    """The four PUTs require an exact-bank configure sentence, never a scalar role.

    The route dependency is deliberately role-free; the service-level gate
    (``credit_params._require_configure``) denies before any row, audit event or
    refresh trigger is touched, and the response is write evidence built from
    this request (D3) — never the read-gated register resolution.
    """
    method, path = key
    matches = [
        route
        for route in app.routes
        if isinstance(route, APIRoute) and route.path == path and method in (route.methods or set())
    ]
    assert len(matches) == 1, f"{method} {path} must have exactly one route"
    dependencies = _dependency_names(matches[0])
    assert required in dependencies
    assert "get_approver_tenant_context" not in dependencies
    assert "get_mutation_tenant_context" not in dependencies
    assert not any(name.startswith("require_role_") for name in dependencies)
    # Configure authority is write authority: no read-gate dependency may sit on
    # the PUT, or a configure-only caller would be denied after commit.
    assert not any(name.startswith("require_credit_") for name in dependencies)


def test_every_register_router_read_is_in_the_authority_table() -> None:
    """A fifth Board register cannot ship on the retired tenancy-only read gate."""
    from app.credit.api import manage_credit_params  # noqa: PLC0415 - router under test

    found = {
        ("GET", f"{_BANK_PREFIX}{route.path.removeprefix('/banks/{bank_id}')}")
        for route in manage_credit_params.router.routes
        if isinstance(route, APIRoute) and "GET" in (route.methods or set())
    }
    assert found == set(_REGISTER_READS), (
        "credit register reads and the authority table disagree; add the route to "
        "_REGISTER_READS and to backend/docs/credit_enforcement_rollout.md"
    )
