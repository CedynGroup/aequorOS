"""FastAPI entrypoint for the cashflow-ml service."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

from app.config import Settings
from app.schemas import (
    ForecastResponse,
    HealthResponse,
    HistoryResponse,
    Horizon,
    TrainResponse,
)
from app.service import ForecastService

_service: ForecastService | None = None


def get_service() -> ForecastService:
    global _service  # noqa: PLW0603 - process-wide singleton keeps the loaded model warm
    if _service is None:
        _service = ForecastService()
    return _service


ServiceDep = Annotated[ForecastService, Depends(get_service)]


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # Load existing artifacts at startup; training stays lazy (first /forecast or /train).
    get_service().load_if_available()
    yield


def create_app() -> FastAPI:
    settings = Settings()
    app = FastAPI(title=settings.app_name, lifespan=lifespan)

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.get("/health", response_model=HealthResponse)
    def health(service: ServiceDep) -> HealthResponse:
        trained = service.is_trained or service.load_if_available()
        return HealthResponse(
            status="ok",
            model_trained=trained,
            model_version=service.model_version,
        )

    @app.post("/train", response_model=TrainResponse)
    def train(service: ServiceDep) -> TrainResponse:
        return TrainResponse.model_validate(service.train())

    @app.get("/forecast", response_model=ForecastResponse)
    def forecast(
        service: ServiceDep,
        horizon: Annotated[Horizon, Query()] = Horizon.DAYS_30,
        mode: Annotated[Literal["lstm", "static"], Query()] = "lstm",
    ) -> ForecastResponse:
        return service.forecast(horizon=int(horizon), mode=mode)

    @app.get("/history", response_model=HistoryResponse)
    def history(
        service: ServiceDep,
        days: Annotated[int, Query(ge=1, le=730)] = 90,
    ) -> HistoryResponse:
        return service.history(days)

    return app


app = create_app()
