"""FX routes must remain on their scoped-binding dependencies."""

from __future__ import annotations

import inspect

from fastapi.routing import APIRoute

from app.core.authorization import Module
from app.main import create_app
from app.services import (
    analysis_workbench,
    scenario_workbench_authorization,
    stress_scenarios,
)

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


def test_fx_shared_workbench_cannot_revert_to_legacy_role_gates() -> None:
    assert scenario_workbench_authorization._SCOPED_MODULES["fx"] is Module.FX
    assert not hasattr(
        scenario_workbench_authorization,
        "require_liquidity_permission",
    )

    guarded_operations = (
        stress_scenarios.list_catalogue,
        stress_scenarios.create_scenario,
        stress_scenarios.update_scenario,
        stress_scenarios.set_archived,
        analysis_workbench.run_analysis,
        analysis_workbench.save_analysis,
        analysis_workbench.list_analyses,
        analysis_workbench.get_analysis,
        analysis_workbench.delete_analysis,
    )
    for operation in guarded_operations:
        source = inspect.getsource(operation)
        assert "require_module_permission(" in source
        assert "get_mutation_tenant_context" not in source
