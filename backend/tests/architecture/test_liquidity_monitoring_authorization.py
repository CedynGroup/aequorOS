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


def _route(path: str, method: str) -> APIRoute:
    routes = [
        route
        for route in create_app().routes
        if isinstance(route, APIRoute) and route.path == path and method in route.methods
    ]
    assert len(routes) == 1, f"{method} {path} must have exactly one route"
    return routes[0]


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


def test_remaining_liquidity_reads_use_scoped_dependencies_without_role_fallback() -> None:
    confidential_paths = (
        "/api/v1/banks/{bank_id}/submissions/bsd3",
        "/api/v1/banks/{bank_id}/liquidity/ewis",
        "/api/v1/banks/{bank_id}/liquidity/cfp",
        "/api/v1/banks/{bank_id}/liquidity/cfp/events",
        "/api/v1/banks/{bank_id}/liquidity-thresholds",
        "/api/v1/banks/{bank_id}/liquidity-haircuts",
        "/api/v1/banks/{bank_id}/cashflow-forecast",
        "/api/v1/banks/{bank_id}/cashflow-history",
        "/api/v1/banks/{bank_id}/analytics/cashflow-window",
        "/api/v1/banks/{bank_id}/sdi/liquidity-position",
    )
    for path in confidential_paths:
        dependencies = _dependency_names(_route(path, "GET"))
        assert "require_liquidity_confidential_view" in dependencies, path
        assert not any(name.startswith("require_role_") for name in dependencies), path
        assert "get_mutation_tenant_context" not in dependencies, path
        assert "get_approver_tenant_context" not in dependencies, path

    dashboard_dependencies = _dependency_names(
        _route("/api/v1/banks/{bank_id}/liquidity/dashboard", "GET")
    )
    assert "require_liquidity_aggregated_view" in dashboard_dependencies
    assert not any(name.startswith("require_role_") for name in dashboard_dependencies)


def test_liquidity_mutations_use_service_enforced_scoped_principal_boundary() -> None:
    mutation_routes = (
        ("POST", "/api/v1/banks/{bank_id}/regulatory-runs"),
        ("POST", "/api/v1/banks/{bank_id}/liquidity/run-all-scenarios"),
        ("PUT", "/api/v1/banks/{bank_id}/liquidity/cfp"),
        ("POST", "/api/v1/banks/{bank_id}/liquidity/cfp/approve"),
        ("POST", "/api/v1/banks/{bank_id}/liquidity/cfp/activate"),
        ("POST", "/api/v1/banks/{bank_id}/liquidity/cfp/de-escalate"),
        ("POST", "/api/v1/banks/{bank_id}/scenario-workbench/{module}/scenarios"),
        (
            "PATCH",
            "/api/v1/banks/{bank_id}/scenario-workbench/{module}/scenarios/{scenario_id}",
        ),
        (
            "POST",
            "/api/v1/banks/{bank_id}/scenario-workbench/{module}/scenarios/{scenario_id}/archive",
        ),
        ("POST", "/api/v1/banks/{bank_id}/scenario-workbench/{module}/analyses"),
        (
            "DELETE",
            "/api/v1/banks/{bank_id}/scenario-workbench/{module}/analyses/{analysis_id}",
        ),
    )
    for method, path in mutation_routes:
        dependencies = _dependency_names(_route(path, method))
        assert "get_scoped_mutation_tenant_context" in dependencies, (method, path)
        assert not any(name.startswith("require_role_") for name in dependencies), (
            method,
            path,
        )
        assert "get_mutation_tenant_context" not in dependencies, (method, path)
        assert "get_approver_tenant_context" not in dependencies, (method, path)


def test_configuration_puts_remain_on_the_held_approver_gate() -> None:
    held_routes = (
        "/api/v1/banks/{bank_id}/liquidity/ewis",
        "/api/v1/banks/{bank_id}/liquidity-thresholds",
        "/api/v1/banks/{bank_id}/liquidity-haircuts",
    )
    for path in held_routes:
        dependencies = _dependency_names(_route(path, "PUT"))
        assert "get_approver_tenant_context" in dependencies, path
        assert "get_scoped_mutation_tenant_context" not in dependencies, path
