"""Liveness and readiness."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.core.context import get_ctx
from app.keys.provider import KeyConfigurationError

router = APIRouter(prefix="/health", tags=["Health"], include_in_schema=False)


class Health(BaseModel):
    status: Literal["ok", "unavailable"]
    checks: dict[str, str]


@router.get("/live")
def live() -> Health:
    return Health(status="ok", checks={})


@router.get("/ready")
def ready(request: Request) -> JSONResponse:
    ctx = get_ctx(request)
    checks: dict[str, str] = {}
    try:
        with ctx.db.engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
        checks["database"] = "ok"
        checks["schema"] = f"ok ({version})" if version else "not migrated"
    except SQLAlchemyError:
        checks.setdefault("database", "unreachable")
        checks.setdefault("schema", "not migrated")
    try:
        ctx.keys.current("receipt")
        ctx.keys.current("audit")
        checks["keys"] = "ok"
    except (KeyConfigurationError, OSError):
        checks["keys"] = "unavailable"
    healthy = all(value.startswith("ok") for value in checks.values())
    body = Health(status="ok" if healthy else "unavailable", checks=checks)
    return JSONResponse(body.model_dump(), status_code=200 if healthy else 503)
