"""Process-wide services handed to routers and the seed."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from fastapi import FastAPI, Request

from app.core.clock import Clock
from app.core.ratelimit import RateLimiter
from app.core.settings import Settings
from app.db.session import Database
from app.keys.provider import KeyProvider

if TYPE_CHECKING:
    from app.enrolment.ca import CertificateAuthority
    from app.enrolment.operators import OperatorClient


@dataclass
class AppContext:
    settings: Settings
    clock: Clock
    keys: KeyProvider
    db: Database
    rate_limiter: RateLimiter = field(default_factory=lambda: RateLimiter(600))
    ca: CertificateAuthority | None = None
    operators: dict[str, OperatorClient] = field(default_factory=lambda: {})


def app_state(request: Request, name: str) -> object:
    """Read an attribute of ``app.state`` (Starlette types ``Request.app`` as ``Any``)."""
    state: object = request.app.state  # pyright: ignore[reportAny]
    return getattr(state, name, None)


def app_context(app: FastAPI) -> AppContext:
    ctx: object = getattr(app.state, "ctx", None)
    assert isinstance(ctx, AppContext)
    return ctx


def get_ctx(request: Request) -> AppContext:
    ctx = app_state(request, "ctx")
    assert isinstance(ctx, AppContext)
    return ctx
