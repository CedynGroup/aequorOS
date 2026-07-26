from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.routing import APIRoute

from app.api.router import api_router
from app.core.config import Settings, get_settings
from app.core.errors import (
    OPENAPI_ERROR_RESPONSES,
    UnhandledExceptionMiddleware,
    register_exception_handlers,
)
from app.core.logging import configure_logging
from app.core.request_id import RequestIdMiddleware
from app.worker import start_inprocess_worker


def generate_operation_id(route: APIRoute) -> str:
    parts = route.name.split("_")
    return parts[0] + "".join(part.title() for part in parts[1:])


def _require_signing_in_production(settings: Settings) -> None:
    """A production deployment that cannot sign cannot file. Say so at boot.

    Signing is required by default, so an unset ``SIGNER_ID_PEPPER`` or
    ``ATTESTATION_SIGNING_ENABLED`` means every statutory return is unfilable.
    That must surface when the container starts, not when an officer opens a
    return on the morning it is due. Non-production keeps running so a developer
    can work on unrelated things; the API reports the same gap per request.
    """
    if settings.app.app_env != "production":
        return
    gaps = settings.attestation.signing_readiness_gaps()
    if gaps:
        msg = (
            "Regulatory returns require signatures, so this deployment cannot "
            f"file anything until {' and '.join(gaps)} is configured. Set them, "
            "or configure a signature-optional signing policy per return."
        )
        raise RuntimeError(msg)


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.logging.log_level)
    _require_signing_in_production(settings)

    app = FastAPI(
        title=settings.app.app_name,
        responses=OPENAPI_ERROR_RESPONSES,
        generate_unique_id_function=generate_operation_id,
    )
    # Middleware is applied outside-in in REVERSE registration order, so this
    # reads inside-out: unhandled exceptions are converted to a 500 below the
    # request-id and CORS layers, which is what lets a browser actually see the
    # error instead of a CORS violation (see UnhandledExceptionMiddleware).
    app.add_middleware(UnhandledExceptionMiddleware)
    app.add_middleware(RequestIdMiddleware)

    if settings.cors.origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors.origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    register_exception_handlers(app)
    app.include_router(api_router, prefix="/api")

    # Optional in-process live-engine worker (off unless RUN_INPROCESS_WORKER).
    start_inprocess_worker()
    return app


app = create_app()
