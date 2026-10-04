"""Markets routes must remain on the exact scoped-binding dependencies."""

from __future__ import annotations

import pytest
from fastapi.routing import APIRoute

from app.main import create_app

_ROUTES = tuple(route for route in create_app().routes if isinstance(route, APIRoute))

_LEGACY_GATES = frozenset(
    {
        "get_mutation_tenant_context",
        "get_approver_tenant_context",
        "get_scoped_mutation_tenant_context",
    }
)


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


@pytest.mark.parametrize(
    ("method", "path", "gate"),
    [
        ("GET", "/api/v1/banks/{bank_id}/market-data/views", "require_markets_published_view"),
        (
            "GET",
            "/api/v1/banks/{bank_id}/market-data/source-preferences",
            "require_markets_published_view",
        ),
        ("GET", "/api/v1/banks/{bank_id}/market-data/planes", "require_markets_published_view"),
        (
            "GET",
            "/api/v1/banks/{bank_id}/market-data/curves/{curve_name}/forward-grid",
            "require_markets_published_view",
        ),
        ("GET", "/api/v1/banks/{bank_id}/market-data/scopes", "require_markets_published_view"),
        ("GET", "/api/v1/banks/{bank_id}/market-data/quota", "require_markets_published_view"),
        ("GET", "/api/v1/market-data/templates/{kind}", "require_markets_template_view"),
        (
            "GET",
            "/api/v1/banks/{bank_id}/implied-rating/runs",
            "require_markets_confidential_view",
        ),
        (
            "GET",
            "/api/v1/banks/{bank_id}/implied-rating/runs/{run_id}",
            "require_markets_confidential_view",
        ),
        (
            "GET",
            "/api/v1/banks/{bank_id}/market-data/overlays",
            "require_markets_confidential_view",
        ),
        (
            "GET",
            "/api/v1/banks/{bank_id}/market-data/connections",
            "require_markets_restricted_view",
        ),
        ("POST", "/api/v1/banks/{bank_id}/implied-rating/runs", "require_markets_run"),
        ("POST", "/api/v1/banks/{bank_id}/market-data/overlays", "require_markets_overlay_create"),
        (
            "POST",
            "/api/v1/banks/{bank_id}/market-data/overlays/{overlay_id}/end",
            "require_markets_overlay_edit",
        ),
        ("POST", "/api/v1/banks/{bank_id}/market-data/uploads", "require_markets_upload"),
    ],
)
def test_markets_routes_carry_exactly_one_scoped_gate(method: str, path: str, gate: str) -> None:
    dependencies = _dependency_names(_route(path, method))

    assert gate in dependencies
    assert "resolve_tenant_bank" in dependencies
    assert not dependencies & _LEGACY_GATES
    assert not any(name.startswith("require_role_") for name in dependencies)
    assert sum(name.startswith("require_markets_") for name in dependencies) == 1


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("PUT", "/api/v1/banks/{bank_id}/market-data/source-preferences"),
        ("POST", "/api/v1/banks/{bank_id}/market-data/connections"),
        (
            "POST",
            "/api/v1/banks/{bank_id}/market-data/connections/{connection_id}/validate",
        ),
        (
            "POST",
            "/api/v1/banks/{bank_id}/market-data/connections/{connection_id}/test",
        ),
        (
            "POST",
            "/api/v1/banks/{bank_id}/market-data/connections/{connection_id}/disable",
        ),
        (
            "POST",
            "/api/v1/banks/{bank_id}/market-data/connections/{connection_id}/enable",
        ),
        ("PATCH", "/api/v1/banks/{bank_id}/market-data/connections/{connection_id}"),
        ("DELETE", "/api/v1/banks/{bank_id}/market-data/connections/{connection_id}"),
    ],
)
def test_configuration_writes_are_held_on_the_legacy_gate(method: str, path: str) -> None:
    """Connection lifecycle and source-preference writes await configuration authority."""
    dependencies = _dependency_names(_route(path, method))

    assert "get_mutation_tenant_context" in dependencies
    assert not any(name.startswith("require_markets_") for name in dependencies)
