"""Liquidity Monitoring must remain on the scoped-binding enforcement gate."""

from __future__ import annotations

from fastapi.routing import APIRoute

from app.main import create_app

_PATH = "/api/v1/banks/{bank_id}/liquidity-monitoring"


def _dependency_names(route: APIRoute) -> set[str]:
    names: set[str] = set()
    stack = [route.dependant]
    while stack:
        dependant = stack.pop()
        if dependant.call is not None:
            names.add(getattr(dependant.call, "__name__", ""))
        stack.extend(dependant.dependencies)
    return names


def test_liquidity_monitoring_route_cannot_revert_to_legacy_authorization() -> None:
    routes = [
        route
        for route in create_app().routes
        if isinstance(route, APIRoute) and route.path == _PATH and "GET" in route.methods
    ]

    assert len(routes) == 1, "Liquidity Monitoring must have exactly one GET detail route"
    dependencies = _dependency_names(routes[0])
    assert "require_liquidity_monitoring_view" in dependencies
    assert not any(name.startswith("require_role_") for name in dependencies)
    assert "get_mutation_tenant_context" not in dependencies
    assert "get_approver_tenant_context" not in dependencies
