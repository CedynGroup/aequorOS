"""Forecasting and reverse-stress routes must remain on their scoped-binding dependencies."""

from __future__ import annotations

from fastapi.routing import APIRoute

from app.main import create_app

_ROUTE_DEPENDENCIES = {
    ("GET", "/api/v1/banks/{bank_id}/forecast/scenarios"): "require_forecasting_aggregated_view",
    ("GET", "/api/v1/banks/{bank_id}/forecast/runs"): "require_forecasting_aggregated_view",
    ("GET", "/api/v1/banks/{bank_id}/forecast/runs/{run_id}"): (
        "require_forecasting_run_detail_view"
    ),
    ("POST", "/api/v1/banks/{bank_id}/forecast/runs"): "require_forecasting_run",
    ("POST", "/api/v1/banks/{bank_id}/forecast/optimizer"): "require_forecasting_run",
    ("POST", "/api/v1/banks/{bank_id}/forecast/whatif"): "require_forecasting_run",
    ("POST", "/api/v1/banks/{bank_id}/reverse-stress/runs"): "require_forecasting_run",
    ("GET", "/api/v1/banks/{bank_id}/reverse-stress/latest"): (
        "require_forecasting_confidential_view"
    ),
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


def test_forecasting_routes_cannot_revert_to_legacy_authorization() -> None:
    routes = [route for route in create_app().routes if isinstance(route, APIRoute)]

    for (method, path), required_dependency in _ROUTE_DEPENDENCIES.items():
        matches = [
            route for route in routes if route.path == path and method in (route.methods or set())
        ]
        assert len(matches) == 1, f"{method} {path} must have exactly one route"
        dependencies = _dependency_names(matches[0])
        assert required_dependency in dependencies, f"{method} {path}"
        assert "get_mutation_tenant_context" not in dependencies
        assert "get_approver_tenant_context" not in dependencies
        assert not any(name.startswith("require_role_") for name in dependencies)


def test_every_forecasting_route_is_covered() -> None:
    """A new forecasting or reverse-stress route must name its binding here."""
    routes = [route for route in create_app().routes if isinstance(route, APIRoute)]
    found = {
        (method, route.path)
        for route in routes
        for method in (route.methods or set())
        if "/forecast/" in route.path or "/reverse-stress/" in route.path
    }
    assert found == set(_ROUTE_DEPENDENCIES)
