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

    # Signing is required for every return by default, so a production deployment
    # that cannot sign cannot file. This check is what makes that visible at
    # deploy time. `create_app` used to raise instead, which took the whole API
    # down over an unrelated capability and locked out the very administrator who
    # could have relaxed the policy — see `_warn_if_signing_unconfigured`.
    #
    # Only production: local and test deployments legitimately run without a
    # signing key, and failing their readiness probe would be noise.
    signing_gaps = settings.attestation.signing_readiness_gaps()
    if signing_gaps and settings.app.app_env == "production":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Attestation signing is not configured "
                f"({' and '.join(signing_gaps)}), so no regulatory return can be "
                "certified or filed. Other capabilities are unaffected."
            ),
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
