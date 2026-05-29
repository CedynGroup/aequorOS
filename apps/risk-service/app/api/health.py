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
        service=settings.app.app_name,
        environment=settings.app.app_env,
        status="ok",
    )


@router.get("/ready", response_model=ReadinessResponse)
def ready(settings: Annotated[Settings, Depends(get_settings)]) -> ReadinessResponse:
    storage_status = "ok" if settings.storage_configured else "misconfigured"
    if settings.database.database_url is None:
        if settings.app.app_env in {"local", "test"}:
            return ReadinessResponse(
                service=settings.app.app_name,
                environment=settings.app.app_env,
                status="ok",
                database={
                    "status": "skipped",
                    "storage": storage_status,
                },
            )

        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database is not configured.",
        )

    engine = get_engine(settings.database.database_url)

    try:
        with Session(engine) as session:
            session.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database connectivity check failed.",
        ) from exc

    if not settings.storage_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Storage is not configured.",
        )

    return ReadinessResponse(
        service=settings.app.app_name,
        environment=settings.app.app_env,
        status="ok",
        database={
            "status": "ok",
            "storage": storage_status,
        },
    )
