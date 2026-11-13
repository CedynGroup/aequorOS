"""Examiner evidence routes must stay on the exact scoped-binding gate.

Issue 180. All three tenant examiner read routes carry one authority sentence
(``require_examiner_evidence``: AUDIT/restricted ``view``, or a verified staff
inspection session) and never a scalar role or a mutation context. This is a
pure structure check, so it fails on a route wired to the wrong dependency even
when no database is available.
"""

from __future__ import annotations

import pytest
from fastapi.routing import APIRoute

from app.main import app

_ROUTES = tuple(route for route in app.routes if isinstance(route, APIRoute))

_LEGACY_GATES = frozenset(
    {
        "get_mutation_tenant_context",
        "get_scoped_mutation_tenant_context",
    }
)

#: Every examiner-dependent API module: the surfaces themselves and the guard.
_EXAMINER_MODULES = (
    "app.reporting.api.examiner_surfaces",
    "app.reporting.api.examiner_authorization",
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
    ("method", "path"),
    [
        ("GET", "/api/v1/banks/{bank_id}/examiner/runs"),
        ("GET", "/api/v1/banks/{bank_id}/examiner/runs/{run_id}/reproduction"),
        ("GET", "/api/v1/banks/{bank_id}/examiner/documentation-package"),
    ],
)
def test_examiner_routes_carry_exactly_one_evidence_gate(method: str, path: str) -> None:
    dependencies = _dependency_names(_route(path, method))

    assert "require_examiner_evidence" in dependencies
    assert "resolve_tenant_bank" in dependencies
    assert not dependencies & _LEGACY_GATES
    assert not any(name.startswith("require_role_") for name in dependencies)
    assert sum(name.startswith("require_examiner_") for name in dependencies) == 1


def test_the_examiner_surface_is_exactly_these_three_routes() -> None:
    """A fourth examiner surface must arrive with its own matrix row and tests."""

    catalogued = {
        (method, route.path)
        for route in _ROUTES
        if route.endpoint.__module__ in _EXAMINER_MODULES and route.include_in_schema
        for method in route.methods
    }
    assert catalogued == {
        ("GET", "/api/v1/banks/{bank_id}/examiner/runs"),
        ("GET", "/api/v1/banks/{bank_id}/examiner/runs/{run_id}/reproduction"),
        ("GET", "/api/v1/banks/{bank_id}/examiner/documentation-package"),
    }
