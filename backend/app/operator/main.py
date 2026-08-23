"""The operator control-plane ASGI app (docs/internal/developer.md §4).

A THIRD entrypoint beside ``app.main`` (tenant API) and ``app.worker``:

    uv run uvicorn app.operator.main:app --port ${OPERATOR_PORT:-8100}

It mounts ONLY operator routers under ``/operator/v1`` plus an unauthenticated
``/operator/health``. It never imports the tenant router, and the tenant app
never imports this module — ``tests/operator/test_route_isolation.py`` is the
permanent guard on both directions.
"""

from __future__ import annotations

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.routing import APIRoute

from app.core.config import (
    get_operator_settings,
    get_settings,
    is_undeployed_environment,
)
from app.core.errors import (
    OPENAPI_ERROR_RESPONSES,
    UnhandledExceptionMiddleware,
    register_exception_handlers,
)
from app.core.logging import configure_logging
from app.core.request_id import RequestIdMiddleware
from app.operator.features.activity import router as activity_router
from app.operator.features.audit_log import router as audit_log_router
from app.operator.features.auth import router as auth_router
from app.operator.features.curves import router as curves_router
from app.operator.features.data_engines import router as data_engines_router
from app.operator.features.desk import router as desk_router
from app.operator.features.fx_forward import router as fx_forward_router
from app.operator.features.inspector import router as inspector_router
from app.operator.features.inspector_fix import router as inspector_fix_router
from app.operator.features.inspector_reads import router as inspector_reads_router
from app.operator.features.jobs import router as jobs_router
from app.operator.features.operating_environment import router as operating_environment_router
from app.operator.features.operators import router as operators_router
from app.operator.features.overview import router as overview_router
from app.operator.features.provision import router as provision_router
from app.operator.features.regulatory_parameters import router as regulatory_parameters_router
from app.operator.features.tenants import router as tenants_router
from app.operator.features.worker_health import router as worker_health_router
from app.schemas.health import HealthResponse

OPERATOR_APP_NAME = "aequoros-operator"


def generate_operation_id(route: APIRoute) -> str:
    parts = route.name.split("_")
    return parts[0] + "".join(part.title() for part in parts[1:])


def create_operator_app() -> FastAPI:
    settings = get_settings()
    operator_settings = get_operator_settings()
    # HARD guard, not a warning: dev-token auth on a DEPLOYED host would be a
    # static shared secret protecting cross-tenant god-mode. The app refuses
    # to exist rather than serve a single request that way. The test is an
    # allow-list of undeployed environments, not ``!= "production"`` — the
    # latter left ``staging`` (same containers, same primary database, a host
    # somebody else can reach) wide open.
    if operator_settings.dev_auth_enabled and not is_undeployed_environment(
        settings.app.app_env
    ):
        msg = (
            f"OPERATOR_DEV_AUTH_ENABLED=1 with APP_ENV={settings.app.app_env}: dev-token "
            "auth exists only for local development and is forbidden on a deployed "
            "environment. Configure workforce OIDC (OPERATOR_OIDC_ISSUER / "
            "OPERATOR_OIDC_CLIENT_ID) and disable dev auth."
        )
        raise RuntimeError(msg)

    configure_logging(settings.logging.log_level)

    app = FastAPI(
        title=OPERATOR_APP_NAME,
        responses=OPENAPI_ERROR_RESPONSES,
        generate_unique_id_function=generate_operation_id,
    )
    # Same middleware order as the tenant app (see app.main for the reasoning:
    # unhandled exceptions become 500s inside the request-id and CORS layers).
    app.add_middleware(UnhandledExceptionMiddleware)
    app.add_middleware(RequestIdMiddleware)
    if operator_settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=operator_settings.cors_origins,
            allow_credentials=True,
            allow_methods=operator_settings.cors_methods,
            allow_headers=operator_settings.cors_headers,
        )
    register_exception_handlers(app)

    @app.get("/operator/health", response_model=HealthResponse, tags=["operator-health"])
    def operator_health() -> HealthResponse:
        return HealthResponse(
            service=OPERATOR_APP_NAME,
            environment=settings.app.app_env,
            status="ok",
        )

    # Session issuance sits beside /operator/health, not under /v1: it is the
    # unauthenticated front door (email+password primary path), not a resource.
    app.include_router(auth_router)

    operator_router = APIRouter(prefix="/operator/v1")
    operator_router.include_router(overview_router)
    operator_router.include_router(tenants_router)
    operator_router.include_router(provision_router)
    operator_router.include_router(activity_router)
    operator_router.include_router(audit_log_router)
    operator_router.include_router(inspector_router)
    operator_router.include_router(inspector_reads_router)
    operator_router.include_router(inspector_fix_router)
    operator_router.include_router(jobs_router)
    operator_router.include_router(worker_health_router)
    operator_router.include_router(data_engines_router)
    operator_router.include_router(desk_router)
    operator_router.include_router(curves_router)
    operator_router.include_router(fx_forward_router)
    operator_router.include_router(operating_environment_router)
    operator_router.include_router(operators_router)
    operator_router.include_router(regulatory_parameters_router)
    app.include_router(operator_router)
    return app


app = create_operator_app()
