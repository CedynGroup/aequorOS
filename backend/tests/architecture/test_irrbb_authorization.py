"""IRRBB routes must remain on service-enforced scoped-binding boundaries."""

from __future__ import annotations

from fastapi.routing import APIRoute

from app.main import create_app

_ROUTES = tuple(route for route in create_app().routes if isinstance(route, APIRoute))


def _dependency_names(route: APIRoute) -> set[str]:
    names: set[str] = set()
    stack = [route.dependant]
    while stack:
        dependant = stack.pop()
        if dependant.call is not None:
            names.add(getattr(dependant.call, "__name__", ""))
        stack.extend(dependant.dependencies)
    return names


def _route(path: str, method: str) -> APIRoute:
    routes = [route for route in _ROUTES if route.path == path and method in route.methods]
    assert len(routes) == 1, f"{method} {path} must have exactly one route"
    return routes[0]


def test_irrbb_run_uses_scoped_mutation_principal_without_role_fallback() -> None:
    dependencies = _dependency_names(
        _route("/api/v1/banks/{bank_id}/irr/run-all-scenarios", "POST")
    )

    assert "get_scoped_mutation_tenant_context" in dependencies
    assert "get_mutation_tenant_context" not in dependencies
    assert "get_approver_tenant_context" not in dependencies
    assert not any(name.startswith("require_role_") for name in dependencies)


def test_irrbb_reads_leave_authority_to_the_exact_service_gate() -> None:
    read_routes = (
        "/api/v1/banks/{bank_id}/irr/dashboard",
        "/api/v1/banks/{bank_id}/irr/ear-analysis",
    )

    for path in read_routes:
        dependencies = _dependency_names(_route(path, "GET"))
        assert "get_tenant_context" in dependencies, path
        assert "get_mutation_tenant_context" not in dependencies, path
        assert "get_approver_tenant_context" not in dependencies, path
        assert not any(name.startswith("require_role_") for name in dependencies), path
