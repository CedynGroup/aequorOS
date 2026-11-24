"""Application factory."""

from __future__ import annotations

import threading
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response

from app.audit.chain import install_append_only_guard
from app.auth.staff import JwksKeyResolver, StaffKeyResolver, http_jwks_fetcher
from app.core.clock import Clock, SystemClock
from app.core.context import AppContext
from app.core.errors import install_error_handlers
from app.core.ratelimit import RateLimiter
from app.core.settings import Settings, get_settings
from app.db.session import Database, make_engine
from app.enrolment.ca import CertificateAuthority, build_ca
from app.enrolment.operators import load_operator_clients
from app.keys.provider import KeyProvider, build_key_provider

API_TITLE = "AequorOS Reporting Gateway"
API_VERSION = "0.1.0"


def _staff_resolver(ctx: AppContext) -> StaffKeyResolver | None:
    settings = ctx.settings
    if settings.uses_builtin_idp:
        from app.idp.tokens import IdpKeyResolver

        return IdpKeyResolver(ctx.keys)
    issuer = settings.effective_oidc_issuer
    if not issuer:
        return None
    return JwksKeyResolver(http_jwks_fetcher(issuer, settings.oidc_jwks_url))


def create_app(
    settings: Settings | None = None,
    *,
    clock: Clock | None = None,
    keys: KeyProvider | None = None,
    database: Database | None = None,
    staff_resolver: StaffKeyResolver | None = None,
    run_scheduler: bool | None = None,
    ca: CertificateAuthority | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    install_append_only_guard()
    ctx = AppContext(
        settings=settings,
        clock=clock or SystemClock(),
        keys=keys or build_key_provider(settings),
        db=database or Database(make_engine(settings.database_url)),
        rate_limiter=RateLimiter(settings.rate_limit_per_minute),
    )
    ctx.ca = ca or build_ca(
        settings.keys_dir / "ca", ctx.clock, local_allowed=settings.uses_local_keys
    )
    ctx.operators = load_operator_clients(settings, ctx.ca, ctx.clock)
    scheduler_on = settings.scheduler_enabled if run_scheduler is None else run_scheduler

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
        stop = threading.Event()
        worker: threading.Thread | None = None
        if scheduler_on:
            from app.jobs import run_scheduler as scheduler_loop

            worker = threading.Thread(
                target=scheduler_loop, args=(ctx, stop), name="srg-scheduler", daemon=True
            )
            worker.start()
        yield
        stop.set()
        if worker is not None:
            worker.join(timeout=5)

    app = FastAPI(
        title=API_TITLE,
        version=API_VERSION,
        lifespan=lifespan,
        docs_url="/docs" if settings.env != "production" else None,
        redoc_url=None,
    )
    app.state.ctx = ctx
    app.state.problem_base = settings.problem_base
    app.state.staff_resolver = staff_resolver or _staff_resolver(ctx)
    if settings.uses_builtin_idp:
        from app.idp.router import IdpState

        app.state.idp = IdpState()
    install_error_handlers(app)

    @app.middleware("http")
    async def count_api_calls(  # pyright: ignore[reportUnusedFunction]
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        path = request.url.path
        if path.startswith(("/v1/", "/oauth/")):
            from app.jobs import record_api_call

            record_api_call(ctx, server_error=response.status_code >= 500)
        return response

    from app.auth.oauth import router as oauth_router
    from app.health.router import router as health_router
    from app.keys.router import router as keys_router

    app.include_router(health_router)
    app.include_router(oauth_router)
    app.include_router(keys_router)
    _include_feature_routers(app)
    if settings.uses_builtin_idp:
        from app.idp.router import router as idp_router

        app.include_router(idp_router)
    return app


def _include_feature_routers(app: FastAPI) -> None:
    from app.routers import feature_routers

    for router in feature_routers():
        app.include_router(router)
