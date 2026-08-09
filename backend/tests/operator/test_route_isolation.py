"""Permanent guard: operator routes never mount on the tenant app, and the
operator app serves nothing but /operator/*.

The whole control-plane security model rests on this separation
(staff_UI.md §1: powerful provisioning capability must never be reachable
from tenant-facing code). If either assertion ever fails, someone wired a
router into the wrong app.
"""

from __future__ import annotations

from fastapi.routing import APIRoute

from app.main import create_app
from app.operator.main import create_operator_app


def _api_paths(app) -> list[str]:  # noqa: ANN001 - FastAPI app
    return [route.path for route in app.routes if isinstance(route, APIRoute)]


def test_tenant_app_has_no_operator_routes() -> None:
    paths = _api_paths(create_app())
    assert paths, "tenant app should expose routes"
    offenders = [path for path in paths if "/operator" in path]
    assert offenders == [], f"operator routes leaked onto the tenant app: {offenders}"


def test_operator_app_serves_only_operator_routes() -> None:
    paths = _api_paths(create_operator_app())
    assert paths, "operator app should expose routes"
    offenders = [path for path in paths if not path.startswith("/operator")]
    assert offenders == [], f"non-operator routes leaked onto the operator app: {offenders}"
    # The exact public surface of this phase — update deliberately.
    assert sorted(paths) == [
        "/operator/health",
        "/operator/v1/data-engines",
        # Market research desk console (spec §11a): duplicates are GET+POST
        # pairs on the same path.
        "/operator/v1/desk/captures",
        "/operator/v1/desk/determinations",
        "/operator/v1/desk/determinations",
        "/operator/v1/desk/determinations/{determination_id}",
        "/operator/v1/desk/determinations/{determination_id}/approve",
        "/operator/v1/desk/determinations/{determination_id}/compute",
        "/operator/v1/desk/determinations/{determination_id}/publish",
        "/operator/v1/desk/determinations/{determination_id}/reject",
        "/operator/v1/desk/determinations/{determination_id}/submit",
        "/operator/v1/desk/determinations/{determination_id}/supersede",
        "/operator/v1/desk/methodologies",
        "/operator/v1/desk/methodologies",
        "/operator/v1/desk/methodologies/ensure-default",
        "/operator/v1/desk/methodologies/{methodology_code}/versions",
        "/operator/v1/desk/methodologies/{methodology_code}/versions/{version}/approve",
        "/operator/v1/desk/observations",
        "/operator/v1/desk/observations",
        "/operator/v1/desk/publications",
        "/operator/v1/tenants",
        "/operator/v1/tenants",
        "/operator/v1/tenants/{org_id}/activity",
    ]
