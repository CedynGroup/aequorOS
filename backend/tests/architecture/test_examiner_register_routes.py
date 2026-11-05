"""The examiner register contract: the allow-list, its routes, and its one copy.

An act-as-examiner session holds no tenant binding, so the ONLY way it reads a
filed register is the kernel allow-list ``app.core.examiner``. Three regressions
matter and none of them is visible from the HTTP tests alone:

* an entry naming a route the app does not serve (the admission would silently
  stop working — a table rot, not a refusal);
* an entry naming a route whose own gate does not consult the examiner contract
  (the route would still answer 403 and the bundle would cite a wall again);
* a SECOND copy of the list appearing in the reporting feature, so the bundle
  discloses a register the tenant API will not open.
"""

from __future__ import annotations

from fastapi.routing import APIRoute

from app.core.examiner import EXAMINER_REGISTER_ROUTES, register_for
from app.main import app
from app.reporting.service import examiner_access

#: The gates that opt into the examiner contract by passing ``request`` to
#: ``_require_institution_permission`` / ``_require_liquidity_view``. A cited route
#: behind any other gate cannot be admitted.
_EXAMINER_AWARE_DEPENDENCIES = frozenset(
    {
        "require_credit_restricted_view",
        "require_capital_confidential_view",
        "require_liquidity_confidential_view",
    }
)


def _dependency_names(route: APIRoute) -> set[str]:
    """Every dependency callable on a route, by name (the gate the route names)."""
    names: set[str] = set()
    stack = [route.dependant]
    while stack:
        dependant = stack.pop()
        if dependant.call is not None:
            names.add(getattr(dependant.call, "__name__", ""))
        stack.extend(dependant.dependencies)
    return names


def _routes(method: str, path: str) -> list[APIRoute]:
    return [
        route
        for route in app.routes
        if isinstance(route, APIRoute) and route.path == path and method in (route.methods or set())
    ]


def test_every_cited_register_is_a_served_read() -> None:
    """Each entry names exactly one route, and it is a GET."""
    for method, path in EXAMINER_REGISTER_ROUTES:
        assert method == "GET", f"{method} {path} is not a read"
        assert len(_routes(method, path)) == 1, f"{method} {path} must have exactly one route"
        # Visibility still decides first: a bank of another tenant is 404 before
        # any examiner admission runs.
        assert "resolve_tenant_bank" in _dependency_names(_routes(method, path)[0])


def test_every_cited_register_is_behind_an_examiner_aware_gate() -> None:
    """A register whose gate ignores the contract would answer 403 forever."""
    for method, path in EXAMINER_REGISTER_ROUTES:
        dependencies = _dependency_names(_routes(method, path)[0])
        assert dependencies & _EXAMINER_AWARE_DEPENDENCIES, (
            f"{method} {path} is in the examiner contract but its gate does not "
            "consult it; pass request= to _require_institution_permission or use "
            "_require_liquidity_view"
        )


def test_the_bundle_cites_exactly_the_registers_the_contract_admits() -> None:
    """One copy of the list: reporting discloses what the kernel admits."""
    disclosed = [
        (name, module, sensitivity)
        for name, module, sensitivity in examiner_access.SOURCE_REGISTERS
    ]
    admitted = [
        (register.name, register.module, register.sensitivity)
        for register in EXAMINER_REGISTER_ROUTES.values()
    ]
    assert disclosed == admitted


def test_the_examination_is_not_a_wider_read_than_the_bundle() -> None:
    """The boundary the captain set, spelled out as data.

    The two credit registers the bundle does not cite, the two SDI diagnostics
    and every register WRITE stay outside the contract, however convenient it
    would be to admit them alongside their neighbours."""
    excluded = {
        "/api/v1/banks/{bank_id}/concentration-limits",
        "/api/v1/banks/{bank_id}/credit-thresholds",
        "/api/v1/banks/{bank_id}/sdi/large-exposures",
        "/api/v1/banks/{bank_id}/sdi/loan-classification",
        "/api/v1/banks/{bank_id}/loan-classification",
        "/api/v1/banks/{bank_id}/liquidity/cfp",
        "/api/v1/banks/{bank_id}/capital-plan/ilaap",
    }
    assert not excluded & {path for _, path in EXAMINER_REGISTER_ROUTES}
    for method, path in EXAMINER_REGISTER_ROUTES:
        assert register_for(method, path) is not None
    for method in ("PUT", "POST", "PATCH", "DELETE"):
        for _, path in EXAMINER_REGISTER_ROUTES:
            assert register_for(method, path) is None, f"{method} {path} must not be admitted"


def test_an_unknown_or_missing_route_admits_nothing() -> None:
    assert register_for("GET", None) is None
    assert register_for("GET", "/api/v1/banks/{bank_id}/not-a-register") is None
    # Case-folded methods are normalised, so a lowercase caller cannot slip past.
    assert register_for("get", "/api/v1/banks/{bank_id}/ecl-assumptions") is not None
