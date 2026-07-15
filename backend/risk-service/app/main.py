from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.routing import APIRoute

from app.api.router import api_router
from app.core.config import get_settings
from app.core.errors import OPENAPI_ERROR_RESPONSES, register_exception_handlers
from app.core.logging import configure_logging
from app.core.request_id import RequestIdMiddleware


def generate_operation_id(route: APIRoute) -> str:
    parts = route.name.split("_")
    return parts[0] + "".join(part.title() for part in parts[1:])


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.logging.log_level)

    app = FastAPI(
        title=settings.app.app_name,
        responses=OPENAPI_ERROR_RESPONSES,
        generate_unique_id_function=generate_operation_id,
    )
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
    return app


app = create_app()
