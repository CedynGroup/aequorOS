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
        # Staff sign-in front door (email+password primary path) — beside
        # /operator/health, not under /v1: session issuance is not a resource.
        "/operator/auth/login",
        "/operator/health",
        # Forward-curve construction console (FC-3): construct is a preview, publish
        # fans out through the desk determination seam. Duplicate 'definitions' is
        # the GET+POST pair on the collection path.
        "/operator/v1/curves/construct",
        "/operator/v1/curves/definitions",
        "/operator/v1/curves/definitions",
        "/operator/v1/curves/definitions/{curve_code}/versions",
        "/operator/v1/curves/definitions/{curve_code}/versions/{version}/approve",
        "/operator/v1/curves/publish",
        "/operator/v1/data-engines",
        # Market research desk console (spec §11a): duplicates are GET+POST
        # pairs on the same path.
        "/operator/v1/desk/captures",
        "/operator/v1/desk/captures/{capture_id}/content",
        "/operator/v1/desk/captures/{capture_id}/snippet",
        "/operator/v1/desk/determinations",
        "/operator/v1/desk/determinations",
        "/operator/v1/desk/determinations/{determination_id}",
        "/operator/v1/desk/determinations/{determination_id}/adjustments",
        "/operator/v1/desk/determinations/{determination_id}/approve",
        "/operator/v1/desk/determinations/{determination_id}/compute",
        "/operator/v1/desk/determinations/{determination_id}/package",
        "/operator/v1/desk/determinations/{determination_id}/publish",
        "/operator/v1/desk/determinations/{determination_id}/reject",
        "/operator/v1/desk/determinations/{determination_id}/submit",
        "/operator/v1/desk/determinations/{determination_id}/supersede",
        "/operator/v1/desk/entitlements",
        "/operator/v1/desk/entitlements/grant-dataset",
        "/operator/v1/desk/entitlements/grant-tier",
        "/operator/v1/desk/entitlements/{entitlement_id}/revoke",
        "/operator/v1/desk/methodologies",
        "/operator/v1/desk/methodologies",
        "/operator/v1/desk/methodologies/ensure-default",
        "/operator/v1/desk/methodologies/{methodology_code}/versions",
        "/operator/v1/desk/methodologies/{methodology_code}/versions/{version}/approve",
        "/operator/v1/desk/observations",
        "/operator/v1/desk/observations",
        "/operator/v1/desk/publications",
        # Operating-Environment desk console: compute-preview writes nothing,
        # the maker-checker lifecycle governs the [0,1] jurisdiction score, and
        # publish fans GHANA_OPERATING_ENVIRONMENT_SCORE out to every tenant.
        # Duplicate 'assessments' is the GET+POST pair on the collection path.
        "/operator/v1/operating-environment/assessments",
        "/operator/v1/operating-environment/assessments",
        "/operator/v1/operating-environment/assessments/{assessment_id}",
        "/operator/v1/operating-environment/assessments/{assessment_id}/approve",
        "/operator/v1/operating-environment/assessments/{assessment_id}/publish",
        "/operator/v1/operating-environment/assessments/{assessment_id}/submit",
        "/operator/v1/operating-environment/compute-preview",
        # Staff account management (operator_admin only): duplicates are the
        # GET+POST pair on the collection path.
        "/operator/v1/operators",
        "/operator/v1/operators",
        "/operator/v1/operators/{email}/deactivate",
        "/operator/v1/operators/{email}/reactivate",
        "/operator/v1/operators/{email}/reset-password",
        "/operator/v1/tenants",
        "/operator/v1/tenants",
        "/operator/v1/tenants/{org_id}/activity",
    ]
