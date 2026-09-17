"""FX routes must remain on their scoped-binding dependencies."""

from __future__ import annotations

from fastapi.routing import APIRoute

from app.main import create_app

_ROUTE_DEPENDENCIES = {
    ("GET", "/api/v1/banks/{bank_id}/fx/dashboard"): "require_fx_aggregated_view",
    ("POST", "/api/v1/banks/{bank_id}/fx/run-all-scenarios"): "require_fx_run",
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


def test_fx_routes_cannot_revert_to_legacy_authorization() -> None:
    routes = [route for route in create_app().routes if isinstance(route, APIRoute)]

    for (method, path), required_dependency in _ROUTE_DEPENDENCIES.items():
        matches = [
            route for route in routes if route.path == path and method in (route.methods or set())
        ]
        assert len(matches) == 1, f"{method} {path} must have exactly one route"
        dependencies = _dependency_names(matches[0])
        assert required_dependency in dependencies
        assert "get_mutation_tenant_context" not in dependencies
        assert not any(name.startswith("require_role_") for name in dependencies)
