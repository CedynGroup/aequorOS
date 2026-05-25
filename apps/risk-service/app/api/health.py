from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.session import get_engine
from app.schemas.health import HealthResponse, ReadinessResponse

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live", response_model=HealthResponse)
def live(settings: Annotated[Settings, Depends(get_settings)]) -> HealthResponse:
    return HealthResponse(
        service=settings.app_name,
        environment=settings.app_env,
        status="ok",
    )


@router.get("/ready", response_model=ReadinessResponse)
def ready(settings: Annotated[Settings, Depends(get_settings)]) -> ReadinessResponse:
    if settings.database_url is None:
        if settings.app_env in {"local", "test"}:
            return ReadinessResponse(
                service=settings.app_name,
                environment=settings.app_env,
                status="ok",
                database={"status": "skipped"},
            )

        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database is not configured.",
        )

    engine = get_engine(settings.database_url)

    try:
        with Session(engine) as session:
            session.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database connectivity check failed.",
        ) from exc

    return ReadinessResponse(
        service=settings.app_name,
        environment=settings.app_env,
        status="ok",
        database={"status": "ok"},
    )
