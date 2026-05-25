from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging
from app.core.request_id import RequestIdMiddleware


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(title=settings.app_name)
    app.add_middleware(RequestIdMiddleware)

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    register_exception_handlers(app)
    app.include_router(api_router, prefix="/api")
    return app


app = create_app()
